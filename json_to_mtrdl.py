"""
SysML 中间JSON → MTRDL 数据结构转换器

将 parsed_model.json（从 SysML 模型解析出的中间文件）转换为 MTRDL 形式化数据结构。

映射规则：
- uml:Class + env          → 变量归入 DataDictionary（Monitored→Σ_in, Logic→Σ_enc, State→Σ_out）
                              约束状态机 → DerivationRule
- uml:Class + Modular      → Module（ownedAttributes → interfaces）
- uml:Class + Mode         → Mode（State Variable → Σ_out）
- uml:Signal + Automic Event → Event(kind=PRIMITIVE), triggerVariable/changetype
- uml:Signal + Undefined Event → Event(kind=PRIMITIVE), domain_type
- uml:Signal + Composite Event → Event(kind=DERIVED), subevent/operator
- uml:StateMachine + constraint statemachine → DerivationRule
- uml:StateMachine + behavioral statemachine → Process
- uml:StateMachine（无stereotype但含FMSysML状态的map_to_SV）→ Process
- uml:Association + refine/arm_of/exclude → Relation（受限：JSON中无端点信息）
- uml:DataType + FiniteIntSet → TypeDefinition
- uml:Dependency + satisfy → 跳过（仅追溯性）
- Requirement（Behavioral/Constraint）→ 跳过（自然语言文本，非形式化MTRDL）

关键JSON结构发现：
- 状态机 regions 中使用 "states" 字段（非 vertices）
- map_to_LV/map_to_SV 在 state 的 fms_annotations 中，不在状态机级别
- transition 有 source_name/target_name 字段
- trigger 通过 triggers 数组引用 signal，每个 trigger 有 signal_name
- Association 没有端点信息（无 memberEnds）
- composite state 有 substate prop，值为逗号分隔的 EA ID
- vertical STDBY 的 StateMachine1 有 null trigger，需跨状态机引用 FPA_mode exit 的 trigger
"""

import json
import os
import sys
import re
from typing import Any, Dict, List, Optional, Set, Tuple

# 修复 Windows 控制台编码问题
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from mtrdl_define import (
    Communication, CommunicationType, Constraint, ConstraintKind,
    DataDictionary, DerivationRule, Event, EventDerivation, EventKind,
    Mode, Module, MTRDLSystem, Process, Relation, StatePredicate,
    Transition, TransitionAction, TypeDefinition, Variable,
)


# ============================================================================
# 工具函数
# ============================================================================

def safe_name(name: str) -> str:
    """将名称中的特殊字符替换为下划线"""
    if not name:
        return "_unnamed_"
    return name.strip()


def find_fms_prop(fms_annotations: list, prop_name: str) -> Optional[str]:
    """从 fms_annotations 中查找指定属性值"""
    if not fms_annotations:
        return None
    for ann in fms_annotations:
        props = ann.get("props", {})
        if prop_name in props:
            val = props[prop_name]
            if val and str(val).strip():
                return str(val).strip()
    return None


def get_stereotypes(element: dict) -> List[str]:
    """获取元素的 stereotype 列表"""
    return element.get("stereotypes", [])


def has_stereotype(element: dict, stereotype: str) -> bool:
    """检查元素是否具有指定 stereotype"""
    return stereotype in get_stereotypes(element)


def var_category(stereotype_list: list) -> str:
    """根据变量 stereotype 判断其 MTRDL 类别"""
    for s in stereotype_list:
        s_lower = s.lower().replace(" ", "_")
        if "monitored" in s_lower:
            return "Monitored"
        if "logic" in s_lower:
            return "Logic"
        if "state" in s_lower and "variable" in s_lower:
            return "State"
    return "Unknown"


# ============================================================================
# 主转换器
# ============================================================================

class SysMLToMTRDLConverter:
    """SysML 中间JSON → MTRDL 转换器"""

    def __init__(self, json_path: str):
        self.json_path = json_path
        with open(json_path, 'r', encoding='utf-8') as f:
            self.data = json.load(f)

        # 全局索引：EA ID → 元素
        self.id_map: Dict[str, dict] = {}
        # 变量 ID → 变量名
        self.var_id_to_name: Dict[str, str] = {}
        # 变量名 → 变量 ID
        self.var_name_to_id: Dict[str, str] = {}
        # 信号 ID → 信号名
        self.signal_id_to_name: Dict[str, str] = {}
        # 信号名 → 信号元素
        self.signal_name_to_elem: Dict[str, dict] = {}

        # MTRDL 数据收集
        self._type_defs: List[TypeDefinition] = []
        self._variables: List[Variable] = []
        self._events: List[Event] = []
        self._modules: List[Module] = []
        self._modes: List[Mode] = []
        self._relations: List[Relation] = []
        self._processes: List[Process] = []
        self._communications: List[Communication] = []
        self._derivation_rules: List[DerivationRule] = []
        self._constraints: List[Constraint] = []

        # 环境变量分类
        self._env_monitored_vars: List[str] = []
        self._env_logic_vars: List[str] = []
        self._env_state_vars: List[str] = []

        # 环境类名称（用于 resolve_owner）
        self._env_class_name: Optional[str] = None

        # 模式名到State Variable属性名的映射
        self._mode_state_vars: Dict[str, str] = {}

        # 事件元信息（用于 Process 的 E_trig 推断）
        self._event_meta: Dict[str, dict] = {}

        # 跨状态机的 trigger 索引：trigger ID → trigger 信息
        self._global_trigger_index: Dict[str, dict] = {}

        # 警告与统计
        self.warnings: List[str] = []

    def convert(self) -> MTRDLSystem:
        """执行完整的转换流程"""
        print("=" * 60)
        print("SysML -> MTRDL 转换开始")
        print("=" * 60)

        # Phase 1: 构建全局索引
        self._build_global_index()
        print(f"[Phase 1] 全局索引构建完成: {len(self.id_map)} 个元素")

        # Phase 2: 提取类型定义
        self._extract_type_definitions()
        print(f"[Phase 2] 类型定义: {len(self._type_defs)} 个")

        # Phase 3: 提取环境类变量 → DataDictionary
        self._extract_environment_class()
        print(f"[Phase 3] 环境变量: {len(self._env_monitored_vars)} Monitored, "
              f"{len(self._env_logic_vars)} Logic, {len(self._env_state_vars)} State")

        # Phase 4: 提取信号 → Events
        self._extract_events()
        print(f"[Phase 4] 事件: {len(self._events)} 个")

        # Phase 5: 提取模块类 → Modules
        self._extract_modules()
        print(f"[Phase 5] 模块: {len(self._modules)} 个")

        # Phase 6: 提取模式类 → Modes
        self._extract_modes()
        print(f"[Phase 6] 模式: {len(self._modes)} 个")

        # Phase 7: 提取关联 → Relations
        self._extract_relations()
        print(f"[Phase 7] 关系: {len(self._relations)} 个")

        # Phase 8: 构建全局 trigger 索引（用于跨状态机 trigger 解析）
        self._build_global_trigger_index()

        # Phase 9: 提取状态机 → Processes + DerivationRules
        self._extract_all_statemachines()
        print(f"[Phase 9] 过程: {len(self._processes)} 个, 推导规则: {len(self._derivation_rules)} 个")

        # Phase 10: 推断通信 → Communications
        self._infer_communications()
        print(f"[Phase 10] 通信: {len(self._communications)} 个")

        # Phase 11: 回填模块/模式的接口（事件部分）
        self._backfill_interfaces()
        print(f"[Phase 11] 接口回填完成")

        # Phase 12: 组装系统
        self._assemble_system()

        # Phase 13: 验证
        errors = self.system.validate()
        if errors:
            print(f"\n[Phase 13] 验证发现 {len(errors)} 个问题:")
            for err in errors[:20]:
                print(f"  - {err}")
            if len(errors) > 20:
                print(f"  ... 还有 {len(errors) - 20} 个问题")
        else:
            print(f"\n[Phase 13] 验证通过！")

        if self.warnings:
            print(f"\n转换警告 ({len(self.warnings)} 个):")
            for w in self.warnings[:15]:
                print(f"  - {w}")
            if len(self.warnings) > 15:
                print(f"  ... 还有 {len(self.warnings) - 15} 个警告")

        print("\n" + "=" * 60)
        print("转换完成！")
        print("=" * 60)

        return self.system

    # ========================================================================
    # Phase 1: 构建全局索引
    # ========================================================================

    def _build_global_index(self):
        """遍历整个 JSON 树，构建 ID → 元素的索引"""
        for pkg in self.data.get("packages", []):
            self._index_element(pkg)
            self._index_children(pkg.get("children", []))

    def _index_children(self, children: list):
        """递归索引子元素"""
        for child in children:
            self._index_element(child)
            # 递归处理嵌套 children
            if "children" in child:
                self._index_children(child["children"])
            # ownedAttributes
            for attr in child.get("ownedAttributes", []):
                self._index_element(attr)
            # ownedBehaviors
            for beh in child.get("ownedBehaviors", []):
                self._index_element(beh)
                # regions → states / transitions
                for region in beh.get("regions", []):
                    for state in region.get("states", []):
                        self._index_element(state)
                    for trans in region.get("transitions", []):
                        self._index_element(trans)
                        # 索引 transition 的 triggers
                        for trig in trans.get("triggers", []):
                            trig_id = trig.get("id", "")
                            if trig_id:
                                self._global_trigger_index[trig_id] = trig

    def _index_element(self, elem: dict):
        """索引单个元素"""
        eid = elem.get("id", "")
        ename = elem.get("name", "")
        if eid:
            self.id_map[eid] = elem
        # 变量和信号索引
        etype = elem.get("type", "")
        if etype == "uml:Property":
            if eid:
                self.var_id_to_name[eid] = ename
                if ename:
                    self.var_name_to_id[ename] = eid
        if etype == "uml:Signal":
            if eid:
                self.signal_id_to_name[eid] = ename
            if ename:
                self.signal_name_to_elem[ename] = elem

    # ========================================================================
    # Phase 2: 提取类型定义
    # ========================================================================

    def _extract_type_definitions(self):
        """从 DataType + FiniteIntSet 提取类型定义"""
        for eid, elem in self.id_map.items():
            if elem.get("type") != "uml:DataType":
                continue
            if not has_stereotype(elem, "FiniteIntSet"):
                continue

            name = safe_name(elem.get("name", "UnknownType"))
            props = {}
            for ann in elem.get("fms_annotations", []):
                props.update(ann.get("props", {}))

            min_val = props.get("min")
            max_val = props.get("max")
            step_val = props.get("step")

            try:
                min_int = int(min_val) if min_val is not None else None
                max_int = int(max_val) if max_val is not None else None
            except (ValueError, TypeError):
                min_int = None
                max_int = None

            td = TypeDefinition(name=name, kind="Int", min=min_int, max=max_int)
            if step_val:
                try:
                    td.unit = f"step={int(step_val)}"
                except (ValueError, TypeError):
                    pass

            if min_int is not None and max_int is not None and min_int > max_int:
                self.warnings.append(
                    f"TypeDefinition '{name}': min({min_int}) > max({max_int}), 已自动互换"
                )
                td.min, td.max = max_int, min_int

            self._type_defs.append(td)

    # ========================================================================
    # Phase 3: 提取环境类变量
    # ========================================================================

    def _extract_environment_class(self):
        """处理 env 类的变量 → DataDictionary.variables + 约束状态机 → DerivationRule"""
        for eid, elem in self.id_map.items():
            if elem.get("type") != "uml:Class":
                continue
            if not has_stereotype(elem, "env"):
                continue

            class_name = safe_name(elem.get("name", "Env"))
            self._env_class_name = class_name
            print(f"  发现环境类: {class_name}")

            # 提取变量
            for attr in elem.get("ownedAttributes", []):
                var_name = safe_name(attr.get("name", ""))
                if not var_name:
                    continue

                category = var_category(get_stereotypes(attr))
                self._add_variable(attr, category, class_name)

                # 分类收集
                if category == "Monitored":
                    self._env_monitored_vars.append(var_name)
                elif category == "Logic":
                    self._env_logic_vars.append(var_name)
                elif category == "State":
                    self._env_state_vars.append(var_name)

            # 环境类的约束状态机 → DerivationRule（在 Phase 9 中处理）

    # ========================================================================
    # Phase 4: 提取事件
    # ========================================================================

    def _extract_events(self):
        """从 Signal 元素提取事件"""
        # 先收集所有 Signal 元素
        signals = []
        for eid, elem in self.id_map.items():
            if elem.get("type") == "uml:Signal":
                signals.append(elem)

        for elem in signals:
            stereotypes = get_stereotypes(elem)
            signal_name = safe_name(elem.get("name", ""))

            if "Automic Event" in stereotypes or "Atomic Event" in stereotypes:
                trigger_var_id = find_fms_prop(elem.get("fms_annotations", []), "triggerVariable")
                change_type = find_fms_prop(elem.get("fms_annotations", []), "changetype")
                trigger_var_name = self._resolve_var_id(trigger_var_id)

                derivation_expr = ""
                if trigger_var_name and change_type:
                    if change_type.lower() == "rising":
                        derivation_expr = f"RISE({trigger_var_name})"
                    elif change_type.lower() == "falling":
                        derivation_expr = f"FALL({trigger_var_name})"
                    else:
                        derivation_expr = f"{trigger_var_name} {change_type}"

                evt = Event(name=signal_name, kind=EventKind.PRIMITIVE, initial_value=False)
                self._events.append(evt)
                self._event_meta[signal_name] = {
                    "kind": "atomic",
                    "triggerVariable": trigger_var_name,
                    "changetype": change_type,
                    "expression": derivation_expr,
                }

            elif "Undefined Event" in stereotypes:
                domain_type = find_fms_prop(elem.get("fms_annotations", []), "domain_type")
                from_inter = find_fms_prop(elem.get("fms_annotations", []), "from_inter")

                evt = Event(name=signal_name, kind=EventKind.PRIMITIVE, initial_value=False)
                self._events.append(evt)
                self._event_meta[signal_name] = {
                    "kind": "undefined",
                    "domain_type": domain_type,
                    "from_inter": from_inter,
                }

            elif "Composite Event" in stereotypes:
                subevent_str = find_fms_prop(elem.get("fms_annotations", []), "subevent")
                operator = find_fms_prop(elem.get("fms_annotations", []), "operator")

                subevent_names = []
                if subevent_str:
                    for ref in subevent_str.split(","):
                        ref = ref.strip()
                        resolved = self._resolve_signal_id(ref)
                        if resolved:
                            subevent_names.append(resolved)
                        else:
                            subevent_names.append(ref)

                derivation_expr = ""
                if subevent_names and operator:
                    derivation_expr = f" {operator.upper()} ".join(subevent_names)

                evt = Event(
                    name=signal_name,
                    kind=EventKind.DERIVED,
                    derivation=EventDerivation(vars=subevent_names, expression=derivation_expr) if derivation_expr else None,
                    initial_value=False,
                )
                self._events.append(evt)
                self._event_meta[signal_name] = {
                    "kind": "composite",
                    "subevents": subevent_names,
                    "operator": operator,
                    "expression": derivation_expr,
                }

    def _resolve_var_id(self, var_id: Optional[str]) -> Optional[str]:
        """通过变量 ID 解析变量名"""
        if not var_id:
            return None
        return self.var_id_to_name.get(var_id, var_id)

    def _resolve_signal_id(self, sig_id: str) -> Optional[str]:
        """通过信号 ID 解析信号名"""
        if not sig_id:
            return None
        return self.signal_id_to_name.get(sig_id, None)

    # ========================================================================
    # Phase 5: 提取模块
    # ========================================================================

    def _extract_modules(self):
        """从 uml:Class + Modular 提取模块"""
        for eid, elem in self.id_map.items():
            if elem.get("type") != "uml:Class":
                continue
            if not has_stereotype(elem, "Modular"):
                continue

            module_name = safe_name(elem.get("name", ""))
            print(f"  发现模块: {module_name}")

            sigma_in, sigma_enc, sigma_out = [], [], []
            e_in, e_enc, e_out = [], [], []

            for attr in elem.get("ownedAttributes", []):
                attr_name = safe_name(attr.get("name", ""))
                if not attr_name:
                    continue
                category = var_category(get_stereotypes(attr))

                if category == "Logic":
                    sigma_enc.append(attr_name)
                elif category == "State":
                    sigma_out.append(attr_name)
                elif category == "Monitored":
                    sigma_in.append(attr_name)
                else:
                    sigma_enc.append(attr_name)

                self._add_variable(attr, category, module_name)

            module = Module(
                name=module_name,
                Sigma_in=sigma_in, E_in=e_in,
                Sigma_enc=sigma_enc, E_enc=e_enc,
                Sigma_out=sigma_out, E_out=e_out,
            )
            self._modules.append(module)

    # ========================================================================
    # Phase 6: 提取模式
    # ========================================================================

    def _extract_modes(self):
        """从 uml:Class + Mode 提取模式"""
        seen_mode_names: Set[str] = set()

        # 按照 JSON children 顺序遍历
        for pkg in self.data.get("packages", []):
            for child in pkg.get("children", []):
                if child.get("type") != "uml:Class":
                    continue
                if not has_stereotype(child, "Mode"):
                    continue

                raw_name = child.get("name", "")
                mode_name = safe_name(raw_name)
                eid = child.get("id", "")

                # 处理名称冲突
                if mode_name in seen_mode_names:
                    alt_name = raw_name + "_arm" if "arm" in eid.lower() else mode_name + f"_{eid[:8]}"
                    self.warnings.append(f"模式名称冲突: '{mode_name}' 已存在，使用 '{alt_name}'")
                    mode_name = alt_name

                seen_mode_names.add(mode_name)
                print(f"  发现模式: {mode_name}")

                sigma_out = []
                sigma_enc = []
                for attr in child.get("ownedAttributes", []):
                    attr_name = safe_name(attr.get("name", ""))
                    if not attr_name:
                        continue
                    category = var_category(get_stereotypes(attr))

                    if category == "State":
                        sigma_out.append(attr_name)
                        self._mode_state_vars[mode_name] = attr_name
                    elif category == "Logic":
                        sigma_enc.append(attr_name)

                    self._add_variable(attr, category, mode_name)

                mode = Mode(name=mode_name, Sigma_out=sigma_out)
                if sigma_enc:
                    mode.Sigma_func = sigma_enc  # Logic 变量 → Σ_func（功能变量）
                self._modes.append(mode)

    def _add_variable(self, attr: dict, category: str, owner: str = "Aircraft State"):
        """确保变量在 DataDictionary 中注册"""
        var_name = safe_name(attr.get("name", ""))
        if not var_name:
            return

        existing_names = {v.name for v in self._variables}
        if var_name in existing_names:
            return

        datatype_id = attr.get("datatype", "")
        datatype_name = self.id_map.get(datatype_id, {}).get("name", "Boolean") if datatype_id else "Boolean"
        default_val = attr.get("defaultValue")

        if category in ("Logic", "State"):
            var_type = "Boolean"
            if default_val is not None:
                default_val = str(default_val).lower() in ("true", "1", "yes")
            else:
                default_val = False
        else:
            var_type = datatype_name if datatype_name else "Real"
            if default_val is not None:
                try:
                    default_val = int(default_val)
                except (ValueError, TypeError):
                    try:
                        default_val = float(default_val)
                    except (ValueError, TypeError):
                        pass

        self._variables.append(Variable(
            name=var_name, type=var_type,
            initial_value=default_val,
            attributes={"stereotype": category, "owner": owner}
        ))

    # ========================================================================
    # Phase 7: 提取关联 → Relations
    # ========================================================================

    # ========================================================================
    # Phase 7: 从精准端点数据中提取关联 → Relations
    # ========================================================================

    def _extract_relations(self):
        """
        【全新修改版】从新版 JSON 的 packages 中递归提取所有关联关系。
        摒弃原先错乱的位置推算，直接利用 XML 导出的高保真端点 ID 进行 100% 精准绑定。
        """
        print("  开始精准提取模式/模块关系...")
        for pkg in self.data.get("packages", []):
            self._process_relations_recursive(pkg)

    def _process_relations_recursive(self, element: dict):
        """递归遍历包/类树，提取其中包含的各种 UML 连线关系"""
        children = element.get("children", []) or []
        
        for child in children:
            # 1. 递归向下探测（支持嵌套的包或类结构）
            if "children" in child and child["children"]:
                self._process_relations_recursive(child)

            # 2. 如果当前节点是连线关系（UML 关联、依赖、实现）
            if child.get("type") in ["uml:Association", "uml:Dependency", "uml:Realization"]:
                stereotypes = child.get("stereotypes", [])
                assoc_name = child.get("name", "")

                # 识别论文第四章定义的三大核心安全性质关系
                rel_type = None
                if "refine" in stereotypes:
                    rel_type = "refine"
                elif "arm_of" in stereotypes:
                    rel_type = "arm_of"
                elif "exclude" in stereotypes:
                    rel_type = "exclude"

                if not rel_type:
                    continue

                endpoints = child.get("endpoints", {})
                e1_name = None
                e2_name = None

                # 优先使用显式的 source 和 target（EA 导出的标准连线属性）
                source_ref = endpoints.get("source_ref")
                target_ref = endpoints.get("target_ref")

                if source_ref and target_ref:
                    # 通过全局 ID 索引表，将端点 EAID 转换为其真正绑定 Class 的人类可读名称
                    e1_name = self.id_map.get(source_ref, {}).get("name")
                    e2_name = self.id_map.get(target_ref, {}).get("name")
                
                # 如果显式端点缺失（ fallback 兼容标准 UML 的 memberEnd 或 ownedEnd 数组）
                if not e1_name or not e2_name:
                    ends = (endpoints.get("member_ends", []) or []) + (endpoints.get("owned_ends", []) or [])
                    if len(ends) >= 2:
                        e1_name = self.id_map.get(ends[0], {}).get("name")
                        e2_name = self.id_map.get(ends[1], {}).get("name")

                # 去除名字前后可能存在的空格空格
                if e1_name: e1_name = e1_name.strip()
                if e2_name: e2_name = e2_name.strip()

                # 如果两个端点的名称都解析失败，说明是孤立或者损坏的线段，打印警告并跳过
                if not e1_name or not e2_name:
                    self.warnings.append(
                        f"连线关系 '{assoc_name or child.get('id')}' ({rel_type}) 缺少有效的端点 Class 引用，已跳过"
                    )
                    continue

                # 实例化 MTRDL 的标准 Relation 结构
                relation = Relation(
                    relType=rel_type,
                    e1=e1_name,
                    e2=e2_name,
                    # 如果连线没有名字，自动用数学公式语义命名 (例如: refine_ASEL_mode_vertical_mode)
                    name=assoc_name if (assoc_name and assoc_name != "_unnamed_") else f"{rel_type}_{e1_name}_{e2_name}",
                    params={"note": "通过修复后的高保真 XML 端点 ID 精准解析"},
                )
                self._relations.append(relation)

    # ========================================================================
    # Phase 8: 构建全局 trigger 索引
    # ========================================================================

    def _build_global_trigger_index(self):
        """构建跨状态机的 trigger 索引，用于解析 null trigger"""
        # 已在 _index_children 中完成
        print(f"  全局 trigger 索引: {len(self._global_trigger_index)} 个 trigger")

    def _resolve_trigger(self, trig: dict) -> Optional[str]:
        """解析 trigger 的事件名
        
        处理三种情况：
        1. trigger 有 signal_name → 直接使用
        2. trigger 有 signal ID → 通过 signal_id_to_name 解析
        3. trigger 全为 null → 通过全局 trigger 索引按 ID 查找同名 trigger
        """
        sig_name = trig.get("signal_name", "")
        if sig_name:
            return sig_name

        sig_id = trig.get("signal", "")
        if sig_id:
            resolved = self.signal_id_to_name.get(sig_id)
            if resolved:
                return resolved

        # 尝试通过 trigger ID 在全局索引中查找非 null 的同名 trigger
        trig_id = trig.get("id", "")
        if trig_id:
            global_trig = self._global_trigger_index.get(trig_id)
            if global_trig and global_trig is not trig:
                gsig_name = global_trig.get("signal_name", "")
                if gsig_name:
                    return gsig_name
                gsig_id = global_trig.get("signal", "")
                if gsig_id:
                    resolved = self.signal_id_to_name.get(gsig_id)
                    if resolved:
                        return resolved

        return None

    # ========================================================================
    # Phase 9: 提取所有状态机
    # ========================================================================

    def _extract_all_statemachines(self):
        """从所有 Class 元素中提取状态机"""
        # 环境类
        for eid, elem in self.id_map.items():
            if elem.get("type") != "uml:Class":
                continue
            if has_stereotype(elem, "env"):
                class_name = safe_name(elem.get("name", ""))
                for beh in elem.get("ownedBehaviors", []):
                    if beh.get("type") != "uml:StateMachine":
                        continue
                    if has_stereotype(beh, "constraint statemachine"):
                        self._convert_constraint_statemachine(beh, class_name)

        # 模块类
        for mod in self._modules:
            elem = self._find_class_elem(mod.name)
            if not elem:
                continue
            for beh in elem.get("ownedBehaviors", []):
                if beh.get("type") != "uml:StateMachine":
                    continue
                if has_stereotype(beh, "behavioral statemachine"):
                    proc = self._convert_behavioral_statemachine(beh, mod.name)
                    if proc:
                        self._processes.append(proc)
                elif has_stereotype(beh, "constraint statemachine"):
                    self._convert_constraint_statemachine(beh, mod.name)
                elif self._has_fm_states(beh):
                    proc = self._convert_behavioral_statemachine(beh, mod.name)
                    if proc:
                        self._processes.append(proc)

        # 模式类
        for mode in self._modes:
            elem = self._find_class_elem(mode.name)
            if not elem:
                continue
            for beh in elem.get("ownedBehaviors", []):
                if beh.get("type") != "uml:StateMachine":
                    continue
                if has_stereotype(beh, "behavioral statemachine"):
                    proc = self._convert_behavioral_statemachine(beh, mode.name)
                    if proc:
                        self._processes.append(proc)
                elif has_stereotype(beh, "constraint statemachine"):
                    self._convert_constraint_statemachine(beh, mode.name)
                elif self._has_fm_states(beh):
                    proc = self._convert_behavioral_statemachine(beh, mode.name)
                    if proc:
                        self._processes.append(proc)

    def _find_class_elem(self, name: str) -> Optional[dict]:
        """通过名称查找 Class 元素"""
        for eid, elem in self.id_map.items():
            if elem.get("type") == "uml:Class" and elem.get("name", "") == name:
                return elem
        return None

    # ========================================================================
    # 约束状态机 → DerivationRule
    # ========================================================================

    def _convert_constraint_statemachine(self, beh: dict, owner_name: str):
        """将约束状态机转换为 DerivationRule"""
        sm_name = safe_name(beh.get("name", ""))

        for region in beh.get("regions", []):
            # 构建 transition ID → transition 映射
            trans_map: Dict[str, dict] = {}
            for trans in region.get("transitions", []):
                trans_map[trans.get("id", "")] = trans

            for state in region.get("states", []):
                if state.get("type") != "uml:State":
                    continue

                state_name = safe_name(state.get("name", ""))
                state_fms = state.get("fms_annotations", [])

                # 查找 map_to_LV 或 map_to_SV
                map_to_lv_id = find_fms_prop(state_fms, "map_to_LV")
                map_to_sv_id = find_fms_prop(state_fms, "map_to_SV")
                state_value = find_fms_prop(state_fms, "value")

                # 解析目标变量名
                target_var = None
                if map_to_lv_id:
                    target_var = self.var_id_to_name.get(map_to_lv_id)
                elif map_to_sv_id:
                    target_var = self.var_id_to_name.get(map_to_sv_id)

                if not target_var:
                    continue

                # 查找 guard 表达式：从入迁移中获取
                guard_expr = None
                incoming_ids = state.get("incoming", [])
                for inc_id in incoming_ids:
                    inc_trans = trans_map.get(inc_id)
                    if inc_trans:
                        g = inc_trans.get("guard")
                        if g:
                            guard_expr = g
                            break

                # 如果没有 guard，使用状态值
                if not guard_expr and state_value:
                    if state_value.lower() == "true":
                        guard_expr = f"DEFAULT_TRUE"
                    elif state_value.lower() == "false":
                        guard_expr = f"DEFAULT_FALSE"

                if target_var and guard_expr:
                    rule = DerivationRule(
                        owner=owner_name,
                        target=target_var,
                        expression=guard_expr,
                        name=f"{sm_name}_{state_name}",
                    )
                    self._derivation_rules.append(rule)
                elif target_var:
                    rule = DerivationRule(
                        owner=owner_name,
                        target=target_var,
                        expression=f"STATE({state_name})",
                        name=f"{sm_name}_{state_name}",
                    )
                    self._derivation_rules.append(rule)

    # ========================================================================
    # 行为状态机 → Process
    # ========================================================================

    def _has_fm_states(self, beh: dict) -> bool:
        """检查状态机是否含有 FMSysML 状态（map_to_SV 或 composite state）"""
        for region in beh.get("regions", []):
            for state in region.get("states", []):
                fms = state.get("fms_annotations", [])
                if find_fms_prop(fms, "map_to_SV"):
                    return True
                if has_stereotype(state, "composite state"):
                    return True
        return False

    def _convert_behavioral_statemachine(self, beh: dict, owner_name: str) -> Optional[Process]:
        """将行为状态机转换为 Process
        
        关键语义规则：
        - 普通状态的 map_to_SV → 加入 Sigma_write（该变量属于 owner 的输出）
        - composite state 的子状态的 map_to_SV → 不加入 Sigma_write（该变量属于其他模式的输出）
          而是加入 Sigma_read（FPA_mode exit 需要读取这些变量来判断状态）
        """
        sm_name = safe_name(beh.get("name", ""))

        # 收集所有 map_to_SV（从状态中），合并为 Sigma_write
        sigma_write_set: Set[str] = set()
        sigma_read_set: Set[str] = set()
        e_trig_list: List[str] = []
        e_trig_set: Set[str] = set()
        state_predicates = []
        transitions = []

        # ID → 状态标签 的映射（在所有 region 遍历后填充）
        state_id_to_label: Dict[str, str] = {}

        # 收集 owner 的输出变量名集合，用于判断 composite state 子状态的 map_to_SV 是否属于 owner
        owner_output_vars: Set[str] = set()
        for mode in self._modes:
            if mode.name == owner_name:
                owner_output_vars = set(mode.Sigma_out)
        for mod in self._modules:
            if mod.name == owner_name:
                owner_output_vars = set(mod.Sigma_out)

        # 第一遍：收集所有状态 ID → 标签映射
        for region in beh.get("regions", []):
            for state in region.get("states", []):
                state_id = state.get("id", "")
                state_name = safe_name(state.get("name", ""))
                if state_id and state_name:
                    state_id_to_label[state_id] = state_name

        # 第二遍：构建状态谓词和收集读写集
        for region in beh.get("regions", []):
            for state in region.get("states", []):
                if state.get("type") not in ("uml:State",):
                    # 跳过 Pseudostate 等
                    continue

                state_name = safe_name(state.get("name", ""))
                state_id = state.get("id", "")

                fms = state.get("fms_annotations", [])
                map_to_sv_id = find_fms_prop(fms, "map_to_SV")
                state_value = find_fms_prop(fms, "value")

                if has_stereotype(state, "composite state"):
                    # 组合状态：解析子状态 ID 为标签名
                    substate_str = find_fms_prop(fms, "substate")
                    sub_ids = [s.strip() for s in substate_str.split(",") if s.strip()] if substate_str else []
                    
                    # 解析子状态标签（先从本状态机的 state_id_to_label 查找，再从全局 id_map 查找）
                    sub_labels = []
                    sub_resolved_vars = []  # (var_name, is_owner_var) 
                    for sid in sub_ids:
                        label = state_id_to_label.get(sid)
                        if not label:
                            sub_elem = self.id_map.get(sid)
                            if sub_elem:
                                label = safe_name(sub_elem.get("name", ""))
                        if label:
                            sub_labels.append(label)
                        else:
                            sub_labels.append(sid)
                        
                        # 获取子状态的 map_to_SV 变量
                        sub_elem = self.id_map.get(sid)
                        if sub_elem:
                            sub_fms = sub_elem.get("fms_annotations", [])
                            sub_map_sv = find_fms_prop(sub_fms, "map_to_SV")
                            if sub_map_sv:
                                resolved = self.var_id_to_name.get(sub_map_sv)
                                if resolved:
                                    is_owner = resolved in owner_output_vars
                                    sub_resolved_vars.append((resolved, is_owner))

                    predicate = f"AND({', '.join(sub_labels)})"
                    state_predicates.append(StatePredicate(label=state_name, predicate=predicate))

                    # 子状态的 map_to_SV 处理：
                    # 属于 owner 的 → Sigma_write
                    # 不属于 owner 的 → Sigma_read（composite state 需要读取其他模式的输出）
                    for var_name, is_owner in sub_resolved_vars:
                        if is_owner:
                            sigma_write_set.add(var_name)
                        sigma_read_set.add(var_name)
                elif map_to_sv_id:
                    resolved_var = self.var_id_to_name.get(map_to_sv_id)
                    if resolved_var:
                        # 只有属于 owner 的输出变量才加入 Sigma_write
                        if resolved_var in owner_output_vars:
                            sigma_write_set.add(resolved_var)
                        sigma_read_set.add(resolved_var)
                        # 根据状态值构建谓词
                        if state_value and state_value.lower() == "true":
                            predicate = f"{resolved_var} == true"
                        elif state_value and state_value.lower() == "false":
                            predicate = f"{resolved_var} == false"
                        else:
                            predicate = f"STATE({state_name})"
                        state_predicates.append(StatePredicate(label=state_name, predicate=predicate))
                    else:
                        state_predicates.append(StatePredicate(label=state_name, predicate=f"STATE({state_name})"))
                else:
                    state_predicates.append(StatePredicate(label=state_name, predicate=f"STATE({state_name})"))

        # 第三遍：收集迁移
        for region in beh.get("regions", []):
            for trans in region.get("transitions", []):
                src_label = trans.get("source_name", "")
                tgt_label = trans.get("target_name", "")

                # Guard
                guard = trans.get("guard")

                # Trigger 事件 - 使用增强的 trigger 解析
                trigger_names = []
                for trig in trans.get("triggers", []):
                    resolved_name = self._resolve_trigger(trig)
                    if resolved_name and resolved_name not in trigger_names:
                        trigger_names.append(resolved_name)

                for tn in trigger_names:
                    if tn not in e_trig_set:
                        e_trig_set.add(tn)
                        e_trig_list.append(tn)

                # 跳过从 Initial 伪状态的迁移
                if src_label == "Initial":
                    continue

                # 构建 transition action（根据目标状态的 map_to_SV 和 value）
                actions = []
                tgt_state_elem = None
                # 通过 target name 找对应 state 元素
                for region2 in beh.get("regions", []):
                    for s in region2.get("states", []):
                        if s.get("name", "") == tgt_label:
                            tgt_state_elem = s
                            break
                    if tgt_state_elem:
                        break

                if tgt_state_elem:
                    tgt_fms = tgt_state_elem.get("fms_annotations", [])
                    tgt_map_sv = find_fms_prop(tgt_fms, "map_to_SV")
                    tgt_value = find_fms_prop(tgt_fms, "value")
                    if tgt_map_sv:
                        tgt_var = self.var_id_to_name.get(tgt_map_sv)
                        if tgt_var and tgt_value:
                            actions.append(TransitionAction(var=tgt_var, expr=tgt_value))

                transition = Transition(
                    source=src_label,
                    target=tgt_label,
                    guard=guard,
                    triggerEvents=trigger_names,
                    actions=actions,
                )
                transitions.append(transition)

        # 从 trigger 事件中推断 sigma_read
        for evt_name in e_trig_list:
            meta = self._event_meta.get(evt_name, {})
            tv = meta.get("triggerVariable")
            if tv:
                sigma_read_set.add(tv)

        # 从 guard 表达式中提取变量引用
        for trans_obj in transitions:
            if trans_obj.guard:
                for vname in self.var_name_to_id:
                    # 简单的子串匹配，可能误匹配
                    if vname in trans_obj.guard:
                        sigma_read_set.add(vname)

        # 检查是否有有效内容
        if not state_predicates and not transitions:
            self.warnings.append(f"过程 '{sm_name}' (owner={owner_name}): 无状态和迁移，跳过")
            return None

        process = Process(
            name=sm_name,
            owner=owner_name,
            Sigma_read=sorted(list(sigma_read_set)),
            Sigma_write=sorted(list(sigma_write_set)),
            E_trig=e_trig_list,
            state_predicates=state_predicates,
            transitions=transitions,
        )
        return process

    # ========================================================================
    # Phase 10: 推断通信 → Communications
    # ========================================================================

    def _infer_communications(self):
        """根据模块/模式之间的变量依赖关系推断通信"""
        # 收集所有模块/模式的输出变量
        all_outputs: Dict[str, Set[str]] = {}
        for mod in self._modules:
            all_outputs[mod.name] = set(mod.Sigma_out)
        for mode in self._modes:
            all_outputs[mode.name] = set(mode.Sigma_out)

        # 同时收集环境变量
        if self._env_class_name:
            env_outputs = set(self._env_monitored_vars) | set(self._env_logic_vars) | set(self._env_state_vars)
            all_outputs[self._env_class_name] = env_outputs

        comm_id = 0
        seen_comms: Set[Tuple[str, str, str]] = set()

        for proc in self._processes:
            owner = proc.owner
            # 跳过自身输出
            owner_outputs = all_outputs.get(owner, set())

            # 检查 Sigma_read 中的变量来源
            for var in proc.Sigma_read:
                if var in owner_outputs:
                    continue

                for sender, outputs in all_outputs.items():
                    if sender == owner:
                        continue
                    if var in outputs:
                        comm_key = (sender, owner, var)
                        if comm_key not in seen_comms:
                            seen_comms.add(comm_key)
                            comm_id += 1
                            self._communications.append(Communication(
                                name=f"comm_{comm_id}",
                                sender=sender,
                                receiver=owner,
                                type=CommunicationType.DATAFLOW,
                                content=[var],
                            ))

            # 检查 E_trig 中事件引用的变量来源
            for evt_name in proc.E_trig:
                meta = self._event_meta.get(evt_name, {})
                trigger_var = meta.get("triggerVariable")
                if not trigger_var or trigger_var in owner_outputs:
                    continue

                for sender, outputs in all_outputs.items():
                    if sender == owner:
                        continue
                    if trigger_var in outputs:
                        comm_key = (sender, owner, f"EVT:{evt_name}")
                        if comm_key not in seen_comms:
                            seen_comms.add(comm_key)
                            comm_id += 1
                            self._communications.append(Communication(
                                name=f"comm_evt_{comm_id}",
                                sender=sender,
                                receiver=owner,
                                type=CommunicationType.EVENT,
                                content=[evt_name],
                            ))

    # ========================================================================
    # Phase 11: 回填接口
    # ========================================================================

    def _backfill_interfaces(self):
        """根据过程和通信信息回填模块/模式的接口（事件部分和输入变量）"""
        # 收集每个 owner 的事件触发集
        owner_e_trig: Dict[str, Set[str]] = {}
        owner_sigma_read: Dict[str, Set[str]] = {}
        owner_sigma_write: Dict[str, Set[str]] = {}

        for proc in self._processes:
            owner = proc.owner
            if owner not in owner_e_trig:
                owner_e_trig[owner] = set()
                owner_sigma_read[owner] = set()
                owner_sigma_write[owner] = set()
            owner_e_trig[owner].update(proc.E_trig)
            owner_sigma_read[owner].update(proc.Sigma_read)
            owner_sigma_write[owner].update(proc.Sigma_write)

        # 收集所有模块/模式的输出变量
        all_outputs: Dict[str, Set[str]] = {}
        for mod in self._modules:
            all_outputs[mod.name] = set(mod.Sigma_out)
        for mode in self._modes:
            all_outputs[mode.name] = set(mode.Sigma_out)

        # 回填模块接口
        for mod in self._modules:
            e_trig = owner_e_trig.get(mod.name, set())
            sigma_read = owner_sigma_read.get(mod.name, set())
            sigma_write = owner_sigma_write.get(mod.name, set())
            own_vars = set(mod.Sigma_out) | set(mod.Sigma_enc)

            # 输入变量：sigma_read 中不属于自身的
            external_reads = sigma_read - own_vars
            if external_reads:
                mod.Sigma_in = sorted(list(set(mod.Sigma_in) | external_reads))

            # 输入事件
            if e_trig:
                mod.E_in = sorted(list(set(mod.E_in) | e_trig))

        # 根据通信推断 E_in 和 E_out（需要在模块和模式都处理完后）
        for comm in self._communications:
            # 接收方的 E_in
            for mod in self._modules:
                if comm.receiver == mod.name and comm.type == CommunicationType.EVENT:
                    mod.E_in = list(set(mod.E_in) | set(comm.content))
            for mode in self._modes:
                if comm.receiver == mode.name and comm.type == CommunicationType.EVENT:
                    for evt in comm.content:
                        if evt not in mode.E_entry and evt not in mode.E_exit:
                            mode.E_run = list(set(mode.E_run) | {evt})

            # 发送方的 E_out
            for mod in self._modules:
                if comm.sender == mod.name and comm.type == CommunicationType.EVENT:
                    mod.E_out = list(set(mod.E_out) | set(comm.content))
            for mode in self._modes:
                if comm.sender == mode.name and comm.type == CommunicationType.EVENT:
                    mode.E_out = list(set(mode.E_out) | set(comm.content))

        # 特殊处理：环境类（Aircraft State）没有自己的过程，但通过推导规则产生事件
        # 将环境类相关的事件添加到其 E_out
        if self._env_class_name:
            for mod in self._modules:
                if mod.name == self._env_class_name:
                    # 查找所有 triggerVariable 指向环境变量的 atomic event
                    for evt_name, meta in self._event_meta.items():
                        tv = meta.get("triggerVariable")
                        if tv and tv in (set(self._env_monitored_vars) | set(self._env_logic_vars) | set(self._env_state_vars)):
                            if evt_name not in mod.E_out:
                                mod.E_out = list(set(mod.E_out) | {evt_name})
                    break
            else:
                # 环境模块还未添加到 self._modules（在 _assemble_system 中才添加）
                # 在此先创建临时环境模块并添加
                env_module = Module(
                    name=self._env_class_name,
                    Sigma_in=[], E_in=[],
                    Sigma_enc=[], E_enc=[],
                    Sigma_out=self._env_monitored_vars + self._env_logic_vars + self._env_state_vars,
                    E_out=[],
                )
                # 查找所有 triggerVariable 指向环境变量的 atomic event
                for evt_name, meta in self._event_meta.items():
                    tv = meta.get("triggerVariable")
                    if tv and tv in (set(self._env_monitored_vars) | set(self._env_logic_vars) | set(self._env_state_vars)):
                        if evt_name not in env_module.E_out:
                            env_module.E_out = list(set(env_module.E_out) | {evt_name})
                self._modules.insert(0, env_module)

        # 回填模式接口
        for mode in self._modes:
            e_trig = owner_e_trig.get(mode.name, set())
            sigma_read = owner_sigma_read.get(mode.name, set())
            sigma_write = owner_sigma_write.get(mode.name, set())
            own_vars = set(mode.Sigma_out) | set(mode.Sigma_int) | set(mode.Sigma_func)

            # 根据 process 名称区分 entry/exit/run
            # 简化策略：所有 E_trig → E_entry, 外部读取变量 → Sigma_run
            if e_trig:
                mode.E_entry = sorted(list(set(mode.E_entry) | e_trig))

            # Sigma_run: 需要从外部读取的变量
            external_reads = sigma_read - own_vars
            if external_reads:
                mode.Sigma_run = sorted(list(set(mode.Sigma_run) | external_reads))

            # 根据通信推断 E_run
            for comm in self._communications:
                if comm.receiver == mode.name and comm.type == CommunicationType.EVENT:
                    for evt in comm.content:
                        if evt not in mode.E_entry and evt not in mode.E_exit:
                            mode.E_run = list(set(mode.E_run) | {evt})

    # ========================================================================
    # Phase 12: 组装系统
    # ========================================================================

    def _assemble_system(self):
        """将所有提取的数据组装为 MTRDLSystem"""
        # 将环境类作为一个伪模块添加（用于 resolve_owner 和通信推断）
        # 如果 _backfill_interfaces 已经添加过则跳过
        env_already_added = any(m.name == self._env_class_name for m in self._modules) if self._env_class_name else False
        if self._env_class_name and not env_already_added:
            env_module = Module(
                name=self._env_class_name,
                Sigma_in=[], E_in=[],
                Sigma_enc=[], E_enc=[],  # 环境类无封装
                Sigma_out=self._env_monitored_vars + self._env_logic_vars + self._env_state_vars,
                E_out=[],
            )
            # 将环境模块插入到模块列表最前面
            self._modules.insert(0, env_module)

        self.system = MTRDLSystem(
            name="FMT_example",
            version="1.0",
            description="Flight Management System - Converted from SysML model",
            dataDictionary=DataDictionary(
                types=self._type_defs,
                variables=self._variables,
                events=self._events,
            ),
            modules=self._modules,
            modes=self._modes,
            relations=self._relations,
            processes=self._processes,
            communications=self._communications,
            derivation_rules=self._derivation_rules,
            constraints=self._constraints,
        )
        self.system.build_index()


# ============================================================================
# 主入口
# ============================================================================

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_path = os.path.join(script_dir, "parsed_model.json")
    output_path = os.path.join(script_dir, "mtrdl_output.json")

    if not os.path.exists(input_path):
        print(f"错误: 找不到输入文件 {input_path}")
        sys.exit(1)

    converter = SysMLToMTRDLConverter(input_path)
    system = converter.convert()

    # 保存
    system.save(output_path)

    # 统计
    print(f"\n转换统计:")
    print(f"  类型定义: {len(system.dataDictionary.types)}")
    print(f"  变量: {len(system.dataDictionary.variables)}")
    print(f"  事件: {len(system.dataDictionary.events)}")
    print(f"  模块: {len(system.modules)}")
    print(f"  模式: {len(system.modes)}")
    print(f"  关系: {len(system.relations)}")
    print(f"  过程: {len(system.processes)}")
    print(f"  通信: {len(system.communications)}")
    print(f"  推导规则: {len(system.derivation_rules)}")
    print(f"  约束: {len(system.constraints)}")

    print(f"\n模块列表:")
    for m in system.modules:
        print(f"  - {m.name}: in={m.Sigma_in}, enc={m.Sigma_enc}, out={m.Sigma_out}, "
              f"E_in={m.E_in}, E_out={m.E_out}")

    print(f"\n模式列表:")
    for m in system.modes:
        print(f"  - {m.name}: out={m.Sigma_out}, entry_ev={m.E_entry}, exit_ev={m.E_exit}, "
              f"run_vars={m.Sigma_run}, run_ev={m.E_run}")

    print(f"\n过程列表:")
    for p in system.processes:
        print(f"  - {p.name} (owner={p.owner}): write={p.Sigma_write}, trig={p.E_trig}, "
              f"states={[sp.label for sp in p.state_predicates]}, trans={len(p.transitions)}")

    print(f"\n推导规则列表:")
    for r in system.derivation_rules:
        print(f"  - {r.name}: {r.owner}.{r.target} = {r.expression}")

    print(f"\n通信列表:")
    for c in system.communications:
        print(f"  - {c.name}: {c.sender} -> {c.receiver} ({c.type.value}: {c.content})")


if __name__ == "__main__":
    main()
