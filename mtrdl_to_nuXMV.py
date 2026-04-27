#!/usr/bin/env python3
"""
MTRDL to NuXMV Converter

"""

import json
import re
from typing import Dict, List, Any, Set, Optional, Tuple
from dataclasses import dataclass, field
from collections import defaultdict


# ============================================================================
# 数据类定义
# ============================================================================

@dataclass
class Variable:
    """MTRDL变量定义"""
    name: str
    var_type: str
    attributes: Dict[str, Any]
    initial_value: Any
    owner: str = ""

    @property
    def stereotype(self) -> str:
        """获取变量构造型: Monitored(Monitor), Logic, State, Control"""
        stereo = self.attributes.get("stereotype", "").lower()
        if stereo in ["monitored", "monitor", "mv"]:
            return "Monitor"
        elif stereo in ["logic", "lv"]:
            return "Logic"
        elif stereo in ["state", "sv"]:
            return "State"
        elif stereo in ["control", "cv"]:
            return "Control"
        return stereo

    @property
    def is_boolean(self) -> bool:
        return self.var_type.lower() in ["boolean", "bool"]

    @property
    def nuXMV_type(self) -> str:
        """映射到NuXMV类型"""
        if self.is_boolean:
            return "boolean"
        elif self.var_type.lower() in ["int", "integer"]:
            return "integer"
        elif self.var_type.lower() in ["real", "float"]:
            return "real"
        return self.var_type


@dataclass
class Event:
    """MTRDL事件定义"""
    name: str
    kind: str  # Primitive, Derived
    derivation: Optional[Dict] = None
    initial_value: Any = False

    @property
    def is_primitive(self) -> bool:
        return self.kind == "Primitive"

    @property
    def is_derived(self) -> bool:
        return self.kind == "Derived"


@dataclass
class Transition:
    """过程迁移定义"""
    source: str
    target: str
    trigger_events: List[str] = field(default_factory=list)
    guard: Optional[str] = None
    actions: List[Dict] = field(default_factory=list)


@dataclass
class Process:
    """MTRDL过程定义"""
    name: str
    owner: str
    sets: Dict[str, List[str]]
    states: List[str]
    state_predicates: Dict[str, str]
    transitions: List[Transition]


@dataclass
class DerivationRule:
    """推导规则"""
    owner: str
    target: str
    expression: str
    name: str = ""


# ============================================================================
# NuXMV代码生成器 (修正版 v2)
# ============================================================================

class NuXMVGenerator:
    """将MTRDL模型转换为NuXMV代码"""

    def __init__(self, mtrdl_model: Dict):
        self.model = mtrdl_model
        self.module_name = mtrdl_model.get("name", "main")

        # 解析模型组件
        self.variables: Dict[str, Variable] = {}
        self.events: Dict[str, Event] = {}
        self.modules: Dict[str, Dict] = {}
        self.modes: Dict[str, Dict] = {}
        self.processes: Dict[str, List[Process]] = defaultdict(list)
        self.derivation_rules: List[DerivationRule] = []
        self.relations: List[Dict] = []
        self.communications: List[Dict] = []

        self._parse_model()

        # 辅助跟踪
        self.aux_vars_per_module: Dict[str, Set[str]] = defaultdict(set)

    def _parse_model(self):
        """解析MTRDL模型的所有组件"""
        dd = self.model.get("dataDictionary", {})

        for var_data in dd.get("variables", []):
            var = Variable(
                name=var_data["name"],
                var_type=var_data["type"],
                attributes=var_data.get("attributes", {}),
                initial_value=var_data.get("initialValue", False),
                owner=var_data.get("attributes", {}).get("owner", "")
            )
            self.variables[var.name] = var

        for evt_data in dd.get("events", []):
            evt = Event(
                name=evt_data["name"],
                kind=evt_data.get("kind", "Primitive"),
                derivation=evt_data.get("derivation"),
                initial_value=evt_data.get("initialValue", False)
            )
            self.events[evt.name] = evt

        for mod_data in self.model.get("modules", []):
            self.modules[mod_data["name"]] = mod_data

        for mode_data in self.model.get("modes", []):
            self.modes[mode_data["name"]] = mode_data

        for proc_data in self.model.get("processes", []):
            process = Process(
                name=proc_data["name"],
                owner=proc_data["owner"],
                sets=proc_data.get("sets", {}),
                states=proc_data.get("states", []),
                state_predicates=proc_data.get("statePredicates", {}),
                transitions=[
                    Transition(
                        source=t["source"],
                        target=t["target"],
                        trigger_events=t.get("triggerEvents", []),
                        guard=t.get("guard"),
                        actions=t.get("actions", [])
                    )
                    for t in proc_data.get("transitions", [])
                ]
            )
            self.processes[process.owner].append(process)

        for rule_data in self.model.get("derivation_rules", []):
            rule = DerivationRule(
                owner=rule_data["owner"],
                target=rule_data["target"],
                expression=rule_data["expression"],
                name=rule_data.get("name", "")
            )
            self.derivation_rules.append(rule)

        self.relations = self.model.get("relations", [])
        self.communications = self.model.get("communications", [])

    def _safe_name(self, name: str) -> str:
        """将名称转换为NuXMV安全标识符"""
        safe = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        if safe and safe[0].isdigit():
            safe = 'v_' + safe
        return safe

    def _nuXMV_value(self, value: Any) -> str:
        """转换值到NuXMV表示"""
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        elif isinstance(value, str):
            if value.lower() in ["true", "false"]:
                return value.upper()
            try:
                float(value)
                return value
            except ValueError:
                return value
        elif isinstance(value, (int, float)):
            return str(value)
        return str(value)

    def _expression_to_nuXMV(self, expr: str) -> str:
        """将MTRDL表达式转换为NuXMV表达式"""
        if not expr:
            return "TRUE"

        result = expr
        result = result.replace('&&', '&').replace('||', '|')
        result = result.replace('=', '==')
        result = re.sub(r'={3,}', '==', result)
        result = re.sub(r'\bTRUE\b', 'TRUE', result, flags=re.IGNORECASE)
        result = re.sub(r'\bFALSE\b', 'FALSE', result, flags=re.IGNORECASE)
        result = result.replace('DEFAULT_TRUE', 'TRUE')
        result = result.replace('DEFAULT_FALSE', 'FALSE')
        return result

    def _detect_pre_usage(self, text: str) -> Set[str]:
        """检测pre()历史引用"""
        pattern = r'pre\s*\(\s*([^)]+)\s*\)'
        return set(re.findall(pattern, text))

    def _get_owned_vars(self, owner_name: str) -> List[Variable]:
        """获取指定所有者拥有的变量"""
        return [v for v in self.variables.values() if v.owner == owner_name]

    def _get_interface_vars(self, entity_name: str, entity_type: str = "module") -> Dict[str, List[str]]:
        """获取实体(模块或模式)的接口变量和事件"""
        result = {
            "input_vars": [],
            "input_events": [],
            "output_vars": [],
            "output_events": [],
            "run_vars": [],
            "run_events": [],
            "entry_vars": [],
            "entry_events": [],
        }

        if entity_type == "module":
            entity_def = self.modules.get(entity_name, {})
        elif entity_type == "mode":
            entity_def = self.modes.get(entity_name, {})
        else:
            return result

        interfaces = entity_def.get("interfaces", {})

        if entity_type == "module":
            result["input_vars"] = interfaces.get("inputs", {}).get("vars", [])
            result["input_events"] = interfaces.get("inputs", {}).get("events", [])
            result["output_vars"] = interfaces.get("outputs", {}).get("vars", [])
            result["output_events"] = interfaces.get("outputs", {}).get("events", [])
        elif entity_type == "mode":
            result["entry_vars"] = interfaces.get("entry", {}).get("vars", [])
            result["entry_events"] = interfaces.get("entry", {}).get("events", [])
            result["run_vars"] = interfaces.get("run", {}).get("vars", [])
            result["run_events"] = interfaces.get("run", {}).get("events", [])
            result["output_vars"] = interfaces.get("output", {}).get("vars", [])
            result["output_events"] = interfaces.get("output", {}).get("events", [])

        return result

    def _find_sender(self, item: str, comm_type: str) -> Optional[str]:
        """查找通信的发送方"""
        for comm in self.communications:
            if comm.get("type") == comm_type and item in comm.get("content", []):
                return comm.get("sender")
        return None

    def _get_mode_output_var(self, mode_name: str) -> Optional[str]:
        """获取模式的输出变量（激活状态变量）"""
        mode_def = self.modes.get(mode_name, {})
        output_vars = mode_def.get("interfaces", {}).get("output", {}).get("vars", [])
        if output_vars:
            return output_vars[0]
        return None

    # ========================================================================
    # 核心生成方法
    # ========================================================================

    def generate_module(self, entity_name: str, entity_type: str = "module") -> str:
        """生成NuXMV模块代码"""
        lines = []
        safe_entity = self._safe_name(entity_name)

        iface = self._get_interface_vars(entity_name, entity_type)

        # 确定参数列表
        params = []
        if entity_type == "module":
            all_input_vars = iface["input_vars"]
            all_input_events = iface["input_events"]
        else:  # mode
            all_input_vars = iface["entry_vars"] + iface["run_vars"]
            all_input_events = iface["entry_events"] + iface["run_events"]

        all_input_vars = list(dict.fromkeys(all_input_vars))
        all_input_events = list(dict.fromkeys(all_input_events))

        for v in all_input_vars:
            params.append(self._safe_name(v))
        for e in all_input_events:
            params.append(self._safe_name(e))

        # MODULE声明
        type_label = "模块" if entity_type == "module" else "模式"
        if params:
            lines.append(f"MODULE {safe_entity}({', '.join(params)})")
        else:
            lines.append(f"MODULE {safe_entity}")
        lines.append(f"-- {type_label}: {entity_name}")
        lines.append("")

        owned_vars = self._get_owned_vars(entity_name)
        owned_processes = self.processes.get(entity_name, [])
        owned_rules = [r for r in self.derivation_rules if r.owner == entity_name]

        # ────────────────────────────────────────
        # VAR: 状态变量声明
        # ────────────────────────────────────────
        lines.append("VAR")

        for var in owned_vars:
            if var.stereotype in ["Logic"]:
                continue
            safe_name = self._safe_name(var.name)
            lines.append(f"    {safe_name} : {var.nuXMV_type};")

        # 辅助历史变量
        for proc in owned_processes:
            for trans in proc.transitions:
                guard_text = trans.guard or ""
                actions_text = " ".join([a.get("expr", "") for a in trans.actions])
                pre_vars = self._detect_pre_usage(guard_text + " " + actions_text)
                for pv in pre_vars:
                    aux_name = f"prev_{self._safe_name(pv)}"
                    if aux_name not in self.aux_vars_per_module[entity_name]:
                        self.aux_vars_per_module[entity_name].add(aux_name)
                        lines.append(f"    {aux_name} : boolean;")

        lines.append("")

        # ────────────────────────────────────────
        # IVAR: 非确定性输入 (仅用于Monitor变量)
        # ────────────────────────────────────────
        ivar_vars = []
        for var in owned_vars:
            if var.stereotype == "Monitor" and var.owner != "env":
                ivar_vars.append(var)

        if ivar_vars:
            lines.append("-- 监控变量作为非确定性输入 (Monitor -> IVAR)")
            lines.append("IVAR")
            for var in ivar_vars:
                lines.append(f"    {self._safe_name(var.name)} : {var.nuXMV_type};")
            lines.append("")

        # ────────────────────────────────────────
        # DEFINE: 宏定义
        # ────────────────────────────────────────
        has_defines = False

        # 派生事件
        for evt_name, evt in self.events.items():
            if evt.is_derived and evt.derivation:
                if not has_defines:
                    lines.append("DEFINE")
                    has_defines = True
                safe_evt = self._safe_name(evt_name)
                expr = self._expression_to_nuXMV(evt.derivation.get("expression", ""))
                for var_name in evt.derivation.get("vars", []):
                    expr = expr.replace(var_name, self._safe_name(var_name))
                lines.append(f"    {safe_evt} := {expr};")

        # 推导规则 (论文4.3.1节: 推导规则 -> DEFINE)
        for rule in owned_rules:
            if not has_defines:
                lines.append("DEFINE")
                has_defines = True
            safe_target = self._safe_name(rule.target)
            expr = self._expression_to_nuXMV(rule.expression)
            lines.append(f"    {safe_target} := {expr};")

        # Logic变量
        for var in owned_vars:
            if var.stereotype == "Logic":
                if not has_defines:
                    lines.append("DEFINE")
                    has_defines = True
                safe_name = self._safe_name(var.name)
                if not any(r.target == var.name for r in owned_rules):
                    lines.append(f"    {safe_name} := {self._nuXMV_value(var.initial_value)};")

        # 状态谓词
        for proc in owned_processes:
            for state_name, predicate in proc.state_predicates.items():
                safe_pred = self._safe_name(f"state_{entity_name}_{state_name}")
                # 检查谓词是否只是简单变量引用，如果是则不需要重复定义
                # 但仍然生成以便在迁移条件中使用
                if not has_defines:
                    lines.append("DEFINE")
                    has_defines = True
                expr = self._expression_to_nuXMV(predicate)
                lines.append(f"    {safe_pred} := {expr};")

        if has_defines:
            lines.append("")

        # ────────────────────────────────────────
        # ASSIGN: 初始状态
        # ────────────────────────────────────────
        lines.append("ASSIGN")

        for var in owned_vars:
            if var.stereotype != "Logic":
                safe_name = self._safe_name(var.name)
                init_val = self._nuXMV_value(var.initial_value)
                lines.append(f"    init({safe_name}) := {init_val};")

        for aux_var in self.aux_vars_per_module[entity_name]:
            orig_name = aux_var.replace("prev_", "")
            lines.append(f"    init({aux_var}) := {orig_name};")

        lines.append("")

        # ────────────────────────────────────────
        # ASSIGN: next() 状态转换
        # ────────────────────────────────────────
        var_transitions: Dict[str, List[Tuple[str, str]]] = defaultdict(list)

        for proc in owned_processes:
            for trans in proc.transitions:
                conditions = []

                # 源状态条件
                if trans.source in proc.state_predicates:
                    source_pred = proc.state_predicates[trans.source]
                    conditions.append(self._expression_to_nuXMV(source_pred))

                # 守卫条件
                if trans.guard:
                    conditions.append(self._expression_to_nuXMV(trans.guard))

                # 触发事件
                for evt in trans.trigger_events:
                    safe_evt = self._safe_name(evt)
                    conditions.append(f"{safe_evt} = TRUE")

                if conditions:
                    cond = " & ".join([f"({c})" for c in conditions])
                else:
                    cond = "TRUE"

                for action in trans.actions:
                    var_name = action["var"]
                    expr = action.get("expr", "TRUE")
                    if isinstance(expr, str):
                        if expr.lower() in ["true", "1"]:
                            nuXMV_expr = "TRUE"
                        elif expr.lower() in ["false", "0"]:
                            nuXMV_expr = "FALSE"
                        else:
                            nuXMV_expr = self._expression_to_nuXMV(expr)
                    else:
                        nuXMV_expr = self._nuXMV_value(expr)

                    safe_var = self._safe_name(var_name)
                    var_transitions[safe_var].append((cond, nuXMV_expr))

        for var_safe, transitions in var_transitions.items():
            if not transitions:
                continue
            lines.append(f"    next({var_safe}) :=")
            lines.append("        case")
            for cond, val in transitions:
                lines.append(f"            {cond} : {val};")
            lines.append(f"            TRUE : {var_safe};  -- default keep")
            lines.append("        esac;")
            lines.append("")

        # 辅助变量更新
        for aux_var in self.aux_vars_per_module[entity_name]:
            orig_name = aux_var.replace("prev_", "")
            lines.append(f"    next({aux_var}) := {orig_name};")

        if self.aux_vars_per_module[entity_name]:
            lines.append("")

        # ────────────────────────────────────────
        # INVAR: 静态不变量约束
        #
        # 注意: 模式互斥(exclude)是时序性质，应使用LTLSPEC
        # INVAR仅用于：
        #   1. 变量取值域约束
        #   2. 物理公理约束
        #   3. 架构级静态约束
        # ────────────────────────────────────────
        invar_rules = []

        # 此处可以添加基于数据字典的取值域约束
        # 例如: 高度变量范围
        for var in owned_vars:
            if var.stereotype == "Monitor" and var.name == "Altitude":
                # 示例: 高度约束 (从类型定义获取)
                safe_name = self._safe_name(var.name)
                invar_rules.append(f"    -- 高度物理约束")
                invar_rules.append(f"    INVAR ({safe_name} >= 0 & {safe_name} <= 10000);")

        if invar_rules:
            lines.append("-- 静态不变量约束 (INVAR)")
            lines.extend(invar_rules)
            lines.append("")

        return "\n".join(lines)

    def generate_main_module(self) -> str:
        """生成顶层main模块"""
        lines = []
        lines.append("MODULE main")
        lines.append("")
        lines.append("-- ════════════════════════════════════════")
        lines.append("-- 顶层模块：系统组成与通信连接")
        lines.append("-- ════════════════════════════════════════")
        lines.append("")

        # ─── 模块实例 ───
        lines.append("-- ─── 模块实例 ───")
        for mod_name in self.modules:
            safe_mod = self._safe_name(mod_name)
            iface = self._get_interface_vars(mod_name, "module")

            actual_params = []
            for var_name in iface["input_vars"]:
                sender = self._find_sender(var_name, "DATAFLOW")
                if sender:
                    actual_params.append(f"{self._safe_name(sender)}.{self._safe_name(var_name)}")
                else:
                    actual_params.append(self._safe_name(var_name))

            for evt_name in iface["input_events"]:
                sender = self._find_sender(evt_name, "EVENT")
                if sender:
                    actual_params.append(f"{self._safe_name(sender)}.{self._safe_name(evt_name)}")
                else:
                    actual_params.append(self._safe_name(evt_name))

            if actual_params:
                lines.append(f"    {safe_mod} : {safe_mod}({', '.join(actual_params)});")
            else:
                lines.append(f"    {safe_mod} : {safe_mod}();")

        lines.append("")

        # ─── 模式实例 ───
        lines.append("-- ─── 模式实例 ───")
        for mode_name in self.modes:
            safe_mode = self._safe_name(mode_name)
            iface = self._get_interface_vars(mode_name, "mode")

            all_input_vars = iface["entry_vars"] + iface["run_vars"]
            all_input_events = iface["entry_events"] + iface["run_events"]
            all_input_vars = list(dict.fromkeys(all_input_vars))
            all_input_events = list(dict.fromkeys(all_input_events))

            actual_params = []
            for var_name in all_input_vars:
                sender = self._find_sender(var_name, "DATAFLOW")
                if sender:
                    actual_params.append(f"{self._safe_name(sender)}.{self._safe_name(var_name)}")
                else:
                    actual_params.append(self._safe_name(var_name))

            for evt_name in all_input_events:
                sender = self._find_sender(evt_name, "EVENT")
                if sender:
                    actual_params.append(f"{self._safe_name(sender)}.{self._safe_name(evt_name)}")
                else:
                    actual_params.append(self._safe_name(evt_name))

            if actual_params:
                lines.append(f"    {safe_mode} : {safe_mode}({', '.join(actual_params)});")
            else:
                lines.append(f"    {safe_mode} : {safe_mode}();")

        lines.append("")

        # ════════════════════════════════════════
        # 性质规约生成 (论文4.4节)
        #
        # 仅生成：
        #   1. 领域约束性质 (模式关系/模块关系/事件关系)
        #   2. FMTM性质 (充分性/必要性/禁止性)
        #
        # 不生成响应性性质 (需人工根据需求定义)
        # ════════════════════════════════════════
        lines.append("-- ════════════════════════════════════════")
        lines.append("-- 性质规约 (LTL/CTL)")
        lines.append("-- ════════════════════════════════════════")
        lines.append("")

        properties = self._generate_properties()
        lines.extend(properties)

        return "\n".join(lines)

    def _generate_properties(self) -> List[str]:
        """
        生成验证性质规约 (论文4.4节)

        生成的性质包括：
          1. 模式关系性质 (exclude/refine/contain/depend/inhibit等)
          2. 模块关系性质 (require/backup等)
          3. FMTM性质框架 (供后续补充具体转换条件)
        """
        lines = []

        # 收集模式输出变量映射
        mode_to_var = {}
        for mode_name in self.modes:
            output_var = self._get_mode_output_var(mode_name)
            if output_var:
                owner = ""
                for var in self.variables.values():
                    if var.name == output_var:
                        owner = var.owner
                        break
                mode_to_var[mode_name] = (output_var, owner)

        # ════════════════════════════════════════
        # 1. 模式关系性质 (基于relations生成LTL)
        # ════════════════════════════════════════
        lines.append("-- ========================================")
        lines.append("-- 模式关系性质 (Mode Relations)")
        lines.append("-- ========================================")
        lines.append("")

        for rel in self.relations:
            e1, e2 = rel.get("e1", ""), rel.get("e2", "")
            rel_type = rel.get("relType", "")

            if e1 not in mode_to_var or e2 not in mode_to_var:
                continue

            v1_name = mode_to_var[e1][0]
            v2_name = mode_to_var[e2][0]
            v1 = self._safe_name(v1_name)
            v2 = self._safe_name(v2_name)
            owner1 = mode_to_var[e1][1]
            owner2 = mode_to_var[e2][1]
            instance1 = self._safe_name(owner1) if owner1 else self._safe_name(e1)
            instance2 = self._safe_name(owner2) if owner2 else self._safe_name(e2)

            if rel_type == "exclude":
                # 论文4.4.2节: mode i Exclude mode j: m_i ∧ m_j = 0
                # 使用LTL全局约束
                lines.append(f"-- Exclude: {e1} 与 {e2} 互斥")
                lines.append(f"-- 数学表达: m_{e1} ∧ m_{e2} = 0")
                lines.append(f"LTLSPEC G !({instance1}.{v1} & {instance2}.{v2});")
                lines.append("")

            elif rel_type == "inhibit":
                # 论文4.4.2节: mode i Inhibit mode j: m_i = 1 ⇒ m_j = 0
                lines.append(f"-- Inhibit: {e1} 抑制 {e2}")
                lines.append(f"-- 数学表达: m_{e1} = 1 ⇒ m_{e2} = 0")
                lines.append(f"LTLSPEC G ({instance1}.{v1} -> !{instance2}.{v2});")
                lines.append("")

            elif rel_type == "depend":
                # 论文4.4.2节: mode i Depend mode j: m_i = 1 ⇒ m_j = 1
                lines.append(f"-- Depend: {e1} 依赖 {e2}")
                lines.append(f"-- 数学表达: m_{e1} = 1 ⇒ m_{e2} = 1")
                lines.append(f"LTLSPEC G ({instance1}.{v1} -> {instance2}.{v2});")
                lines.append("")

            elif rel_type == "refine":
                # refine: 子模式激活时父模式必须激活
                # 论文4.3.2节: Refine关系
                lines.append(f"-- Refine: {e1} 细化 {e2}")
                lines.append(f"-- {e1}激活 ⇒ {e2}激活")
                lines.append(f"LTLSPEC G ({instance1}.{v1} -> {instance2}.{v2});")
                lines.append("")

            elif rel_type == "contain":
                # 论文4.4.2节: mode i Contain some modes:
                # m_i = 1 ⇒ ⋀_{j∈sub(i)} m_j = 1
                lines.append(f"-- Contain: {e1} 包含 {e2}")
                lines.append(f"LTLSPEC G ({instance1}.{v1} -> {instance2}.{v2});")
                lines.append("")

            elif rel_type == "armmode":
                # 论文4.4.2节: mode i Armmode mode j: m_i = 1 ⇒ m_j = 0
                lines.append(f"-- Armmode: {e1} 预位 {e2}")
                lines.append(f"LTLSPEC G ({instance1}.{v1} -> !{instance2}.{v2});")
                lines.append("")

            elif rel_type == "nextmode":
                # 论文4.4.2节: 后继模式
                # falling(M_A) ⇒ X M_B
                lines.append(f"-- Nextmode: {e1} → {e2}")
                lines.append(f"-- falling({e1}) ⇒ X {e2}")
                falling = f"({instance1}.{v1} & X !{instance1}.{v1})"
                lines.append(f"LTLSPEC G ({falling} -> X {instance2}.{v2});")
                lines.append("")

        # ════════════════════════════════════════
        # 2. 模块关系性质 (Module Relations)
        # ════════════════════════════════════════
        lines.append("-- ========================================")
        lines.append("-- 模块关系性质 (Module Relations)")
        lines.append("-- ========================================")
        lines.append("")

        # 查找模块间的PrimaryBackup关系
        for rel in self.relations:
            e1, e2 = rel.get("e1", ""), rel.get("e2", "")
            rel_type = rel.get("relType", "")

            if rel_type == "require":
                # 模块可用性依赖
                # 查找可用性变量
                avail_var_e1 = None
                avail_var_e2 = None
                for var in self.variables.values():
                    if var.owner == e1 and "avail" in var.name.lower():
                        avail_var_e1 = var
                    if var.owner == e2 and "avail" in var.name.lower():
                        avail_var_e2 = var

                if avail_var_e1 and avail_var_e2:
                    v1 = self._safe_name(avail_var_e1.name)
                    v2 = self._safe_name(avail_var_e2.name)
                    o1 = self._safe_name(e1)
                    o2 = self._safe_name(e2)
                    lines.append(f"-- Require: {e1} 需要 {e2}")
                    lines.append(f"LTLSPEC G ({o1}.{v1} -> {o2}.{v2});")
                    lines.append("")

        # ════════════════════════════════════════
        # 3. FMTM性质框架 (基于论文4.4.1节)
        # ════════════════════════════════════════
        lines.append("-- ========================================")
        lines.append("-- FMTM 飞行模式转换矩阵性质")
        lines.append("-- ========================================")
        lines.append("")
        lines.append("-- 以下为FMTM性质框架，具体触发条件需根据实际需求补充")
        lines.append("")
        lines.append("-- 充分性 (Sufficiency):")
        lines.append("-- G((M_i & trigger(i,j)) -> F(M_j))")
        lines.append("")
        lines.append("-- 必要性 (Necessity):")
        lines.append("-- G((M_i & X M_j) -> recent(trigger(i,j)))")
        lines.append("")
        lines.append("-- 禁止性 (Prohibition):")
        lines.append("-- AG(M_i -> AX(!M_j))  对所有FMTM中未允许的转换")
        lines.append("")

        return lines

    def generate_nuXMV(self, output_file: str = None) -> str:
        """生成完整的NuXMV代码"""
        lines = []

        # 文件头
        lines.append(f"-- {'=' * 50}")
        lines.append(f"-- NuXMV Model: {self.module_name}")
        lines.append(f"-- Generated from MTRDL model v{self.model.get('version', '1.0')}")
        lines.append(f"-- Description: {self.model.get('description', '')}")
        lines.append(f"-- {'=' * 50}")
        lines.append("")
        lines.append("-- 生成说明:")
        lines.append("--   1. Monitor变量 → IVAR (非确定性输入)")
        lines.append("--   2. Logic变量 → DEFINE (组合逻辑宏)")
        lines.append("--   3. State/Control变量 → VAR (状态变量)")
        lines.append("--   4. 推导规则 → DEFINE")
        lines.append("--   5. 模式关系 → LTLSPEC (时序性质)")
        lines.append("--   6. 静态约束 → INVAR (不变量)")
        lines.append("")

        # 生成模块定义
        for mod_name in self.modules:
            lines.append(f"-- {'─' * 50}")
            lines.append(f"-- 模块定义: {mod_name}")
            lines.append(f"-- {'─' * 50}")
            lines.append(self.generate_module(mod_name, "module"))
            lines.append("")

        # 生成模式定义
        for mode_name in self.modes:
            lines.append(f"-- {'─' * 50}")
            lines.append(f"-- 模式定义: {mode_name}")
            lines.append(f"-- {'─' * 50}")
            lines.append(self.generate_module(mode_name, "mode"))
            lines.append("")

        # 生成main模块
        lines.append(self.generate_main_module())

        result = "\n".join(lines)

        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(result)
            print(f"NuXMV代码已输出至: {output_file}")

        return result


# ============================================================================
# 主函数
# ============================================================================

def convert_mtrdl_to_nuXMV(input_json: str, output_xmv: str = None) -> str:
    """将MTRDL JSON文件转换为NuXMV代码"""
    with open(input_json, 'r', encoding='utf-8') as f:
        mtrdl_model = json.load(f)

    generator = NuXMVGenerator(mtrdl_model)
    nuXMV_code = generator.generate_nuXMV(output_xmv)

    return nuXMV_code


def print_stats(model_path: str):
    """打印转换统计信息"""
    with open(model_path, 'r', encoding='utf-8') as f:
        model = json.load(f)

    dd = model.get("dataDictionary", {})
    variables = dd.get("variables", [])
    events = dd.get("events", [])
    modules_list = model.get("modules", [])
    modes_list = model.get("modes", [])
    processes_list = model.get("processes", [])
    relations_list = model.get("relations", [])

    logic_count = sum(1 for v in variables
                      if v.get("attributes", {}).get("stereotype", "").lower() in ["logic", "lv"])
    state_count = sum(1 for v in variables
                      if v.get("attributes", {}).get("stereotype", "").lower() in ["state", "sv"])
    monitor_count = sum(1 for v in variables
                        if v.get("attributes", {}).get("stereotype", "").lower() in ["monitored", "monitor", "mv"])
    control_count = sum(1 for v in variables
                        if v.get("attributes", {}).get("stereotype", "").lower() in ["control", "cv"])

    primitive_events = sum(1 for e in events if e.get("kind") == "Primitive")
    derived_events = sum(1 for e in events if e.get("kind") == "Derived")

    # 统计关系类型
    rel_types = defaultdict(int)
    for rel in relations_list:
        rel_types[rel.get("relType", "unknown")] += 1

    print("\n" + "=" * 60)
    print("  MTRDL -> NuXMV 转换统计")
    print("=" * 60)
    print(f"  变量总数:       {len(variables)}")
    print(f"    - Monitor:     {monitor_count} (-> IVAR)")
    print(f"    - Logic:       {logic_count} (-> DEFINE)")
    print(f"    - State:       {state_count} (-> VAR)")
    print(f"    - Control:     {control_count} (-> VAR)")
    print(f"  事件总数:       {len(events)}")
    print(f"    - Primitive:   {primitive_events}")
    print(f"    - Derived:     {derived_events}")
    print(f"  模块数:         {len(modules_list)}")
    print(f"  模式数:         {len(modes_list)}")
    print(f"  过程数:         {len(processes_list)}")
    print(f"  关系数:         {len(relations_list)}")
    for rtype, count in sorted(rel_types.items()):
        print(f"    - {rtype}:     {count}")
    print(f"  通信链路:       {len(model.get('communications', []))}")
    print("=" * 60)


def main():
    """命令行入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description='MTRDL to NuXMV Converter - 将MTRDL模型转换为NuXMV验证代码'
    )
    parser.add_argument('input', help='输入的MTRDL JSON文件路径')
    parser.add_argument('-o', '--output', default=None, help='输出NuXMV文件路径')
    parser.add_argument('--stats', action='store_true', help='显示转换统计信息')

    args = parser.parse_args()

    if args.stats:
        print_stats(args.input)

    nuXMV_code = convert_mtrdl_to_nuXMV(args.input, args.output)

    if args.output is None:
        print(nuXMV_code)


if __name__ == "__main__":
    main()