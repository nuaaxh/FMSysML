"""
MTRDL 完整形式化数据结构
严格遵循论文形式化定义，缺失部分根据描述合理推断补充

系统定义（数学顺序）：S = ⟨M_o, M_d, R, P, C, D, F, Φ⟩
注：JSON 输出的物理顺序为：D, modules(M_o), modes(M_d), R, P, C, F, Φ
    这与数学顺序不同，但不影响语义等价性
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Set, Union, Tuple
from enum import Enum
import json
from collections import defaultdict


# ============================================================================
# 基础枚举类型
# ============================================================================

class EventKind(Enum):
    """
    事件类型 - 对应 π₁(E_meta(e))
    
    论文定义：
    - Primitive: 真值完全由外部环境输入决定
    - Derived: 真值由派生表达式计算得到
    """
    PRIMITIVE = "Primitive"
    DERIVED = "Derived"


class RelationType(Enum):
    """
    结构关系类型 - relType ∈ RelTypes
    
    论文定义：关系类型是来自领域本体库的标识符
    """
    CONTAINS = "contains"           # 包含关系（模块包含子模块，模式包含子模式）
    OWNS = "owns"                   # 拥有关系（模块拥有模式）
    REFERENCES = "references"       # 引用关系
    DEPENDS_ON = "dependsOn"        # 依赖关系
    IMPLEMENTS = "implements"       # 实现关系
    TRANSITIONS_TO = "transitionsTo"  # 模式转换关系


class CommunicationType(Enum):
    """
    通信类型 - type_c ∈ {DATAFLOW, EVENT}
    
    论文定义：
    - DATAFLOW: 传递变量的值
    - EVENT: 传递事件的发生
    """
    DATAFLOW = "DATAFLOW"
    EVENT = "EVENT"


class ConstraintKind(Enum):
    """
    约束类型 - kind_φ
    
    推断定义：
    - INVARIANT: 系统必须始终保持的不变式
    - ASSUMPTION: 对环境的假设
    - GUARANTEE: 系统提供的保证
    """
    INVARIANT = "invariant"
    ASSUMPTION = "assumption"
    GUARANTEE = "guarantee"


# ============================================================================
# 数据字典 D
# ============================================================================

@dataclass
class TypeDefinition:
    """
    类型定义
    
    推断定义：类型定义了变量的值域范围
    """
    name: str                                    # 类型名称
    kind: str                                    # Int, Boolean, Real, Enum
    min: Optional[Union[int, float]] = None      # 最小值约束
    max: Optional[Union[int, float]] = None      # 最大值约束
    enum_values: Optional[List[str]] = None      # 枚举值列表
    unit: Optional[str] = None                   # 物理单位
    
    def to_dict(self) -> dict:
        d = {"name": self.name, "kind": self.kind}
        if self.min is not None:
            d["min"] = self.min
        if self.max is not None:
            d["max"] = self.max
        if self.enum_values:
            d["enumValues"] = self.enum_values
        if self.unit:
            d["unit"] = self.unit
        return d


@dataclass
class Variable:
    """
    变量 - 对应 Σ_all 中的元素
    
    论文定义：Σ_all 是系统中所有变量的有限集合
    元信息映射 M_Σ: Σ_all → Value × Attributes
    """
    name: str                                    # 变量名
    type: str                                    # 类型引用
    initial_value: Optional[Union[int, float, bool, str]] = None  # 初始值
    attributes: Dict[str, Any] = field(default_factory=dict)      # 属性（如 stereotype）
    
    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "type": self.type,
            "attributes": self.attributes
        }
        if self.initial_value is not None:
            d["initialValue"] = self.initial_value
        return d


@dataclass
class EventDerivation:
    """
    派生事件推导规则
    
    论文定义：对应 π₂(E_meta(e)) = (vars_e, φ_e)
    - vars_e ⊆ Σ_all 是表达式 φ_e 经递归展开后依赖的基础变量集合
    - φ_e ∈ Expr 是定义在 vars_e 上的布尔表达式
    """
    vars: List[str]                              # vars_e - 依赖的基础变量
    expression: str                              # φ_e - 布尔表达式
    
    def to_dict(self) -> dict:
        return {"vars": self.vars, "expression": self.expression}


@dataclass
class Event:
    """
    事件 - 对应 E_all 中的元素
    
    论文定义：
    - E_all 是系统中所有事件的有限集合
    - Σ_all ∩ E_all = ∅
    - 元信息映射 E_meta: E_all → EventKind × (vars × Expr)?
    """
    name: str                                    # 事件名
    kind: EventKind = EventKind.PRIMITIVE        # 事件类型
    derivation: Optional[EventDerivation] = None # 派生规则（仅对 Derived 有效）
    initial_value: Optional[bool] = None         # 初始真值
    
    def to_dict(self) -> dict:
        d = {"name": self.name, "kind": self.kind.value}
        if self.derivation:
            d["derivation"] = self.derivation.to_dict()
        if self.initial_value is not None:
            d["initialValue"] = self.initial_value
        return d


@dataclass
class DataDictionary:
    """
    数据字典 D
    
    论文定义：为系统模型中使用的所有变量、事件、类型及其语义提供形式化基础
    """
    types: List[TypeDefinition] = field(default_factory=list)
    variables: List[Variable] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    
    # 索引（运行时构建）
    _var_index: Dict[str, Variable] = field(default_factory=dict, repr=False)
    _event_index: Dict[str, Event] = field(default_factory=dict, repr=False)
    _type_index: Dict[str, TypeDefinition] = field(default_factory=dict, repr=False)
    
    def build_index(self):
        """构建索引"""
        self._var_index = {v.name: v for v in self.variables}
        self._event_index = {e.name: e for e in self.events}
        self._type_index = {t.name: t for t in self.types}
    
    def get_variable(self, name: str) -> Optional[Variable]:
        return self._var_index.get(name)
    
    def get_event(self, name: str) -> Optional[Event]:
        return self._event_index.get(name)
    
    def get_type(self, name: str) -> Optional[TypeDefinition]:
        return self._type_index.get(name)
    
    def to_dict(self) -> dict:
        d = {}
        if self.types:
            d["types"] = [t.to_dict() for t in self.types]
        if self.variables:
            d["variables"] = [v.to_dict() for v in self.variables]
        if self.events:
            d["events"] = [e.to_dict() for e in self.events]
        return d


# ============================================================================
# 模块 M_o - 七元组
# ============================================================================

@dataclass
class Module:
    """
    模块 m ∈ M_o
    
    论文定义：m = ⟨name_m, Σ_in, E_in, Σ_enc, E_enc, Σ_out, E_out⟩
    
    语义：
    - 输入变量 Σ_in：模块对外部环境或其他模块状态的依赖，只读
    - 内部封装变量 Σ_enc：模块的私有状态和内部控制逻辑
    - 输出变量 Σ_out：模块对外可观测的状态或控制结果
    - 事件不属于可写状态，划分仅用于限定可见性边界
    """
    name: str                                    # name_m - 唯一标识符
    
    # 输入
    Sigma_in: List[str] = field(default_factory=list)   # Σ_in - 输入变量
    E_in: List[str] = field(default_factory=list)       # E_in - 输入事件
    
    # 内部封装
    Sigma_enc: List[str] = field(default_factory=list)  # Σ_enc - 内部封装变量
    E_enc: List[str] = field(default_factory=list)      # E_enc - 内部封装事件
    
    # 输出
    Sigma_out: List[str] = field(default_factory=list)  # Σ_out - 输出变量
    E_out: List[str] = field(default_factory=list)      # E_out - 输出事件
    
    def to_dict(self) -> dict:
        d = {"name": self.name}
        interfaces = {}
        
        if self.Sigma_in or self.E_in:
            interfaces["inputs"] = {}
            if self.Sigma_in:
                interfaces["inputs"]["vars"] = self.Sigma_in
            if self.E_in:
                interfaces["inputs"]["events"] = self.E_in
        
        if self.Sigma_enc or self.E_enc:
            interfaces["encapsulated"] = {}
            if self.Sigma_enc:
                interfaces["encapsulated"]["vars"] = self.Sigma_enc
            if self.E_enc:
                interfaces["encapsulated"]["events"] = self.E_enc
        
        if self.Sigma_out or self.E_out:
            interfaces["outputs"] = {}
            if self.Sigma_out:
                interfaces["outputs"]["vars"] = self.Sigma_out
            if self.E_out:
                interfaces["outputs"]["events"] = self.E_out
        
        if interfaces:
            d["interfaces"] = interfaces
        return d
    
    def get_all_outputs(self) -> Set[str]:
        """获取模块的所有输出（变量+事件）"""
        return set(self.Sigma_out) | set(self.E_out)
    
    def get_effective_inputs(self) -> Tuple[Set[str], Set[str]]:
        """获取有效输入（变量, 事件）"""
        return set(self.Sigma_in), set(self.E_in)
    
    def get_effective_encapsulated(self) -> Tuple[Set[str], Set[str]]:
        """获取有效封装（变量, 事件）"""
        return set(self.Sigma_enc), set(self.E_enc)


# ============================================================================
# 模式 M_d - 十二元组
# ============================================================================

@dataclass
class Mode:
    """
    模式 d ∈ M_d
    
    论文定义：
    d = ⟨name_d, Σ_entry, E_entry, Σ_exit, E_exit, Σ_run, E_run,
         Σ_int, Σ_func, E_int, Σ_out, E_out⟩
    
    语义：
    - entry/exit: 定义模式激活与去激活的判定边界
    - run: 模式处于激活或未激活期间的所需输入
    - int/func: 内部控制流状态与功能特性，实现"控制"与"功能"的解耦
    - output: 模式对系统其他部分的可见影响
    """
    name: str                                    # name_d - 唯一标识符
    
    # 接通逻辑输入
    Sigma_entry: List[str] = field(default_factory=list)  # Σ_entry
    E_entry: List[str] = field(default_factory=list)      # E_entry
    
    # 切出逻辑输入
    Sigma_exit: List[str] = field(default_factory=list)   # Σ_exit
    E_exit: List[str] = field(default_factory=list)       # E_exit
    
    # 运行时输入
    Sigma_run: List[str] = field(default_factory=list)    # Σ_run
    E_run: List[str] = field(default_factory=list)        # E_run
    
    # 内部
    Sigma_int: List[str] = field(default_factory=list)    # Σ_int - 内部控制流变量
    Sigma_func: List[str] = field(default_factory=list)   # Σ_func - 功能变量
    E_int: List[str] = field(default_factory=list)        # E_int - 内部事件
    
    # 输出
    Sigma_out: List[str] = field(default_factory=list)    # Σ_out - 输出变量
    E_out: List[str] = field(default_factory=list)        # E_out - 输出事件
    
    def to_dict(self) -> dict:
        d = {"name": self.name}
        interfaces = {}
        
        if self.Sigma_entry or self.E_entry:
            interfaces["entry"] = {}
            if self.Sigma_entry:
                interfaces["entry"]["vars"] = self.Sigma_entry
            if self.E_entry:
                interfaces["entry"]["events"] = self.E_entry
        
        if self.Sigma_exit or self.E_exit:
            interfaces["exit"] = {}
            if self.Sigma_exit:
                interfaces["exit"]["vars"] = self.Sigma_exit
            if self.E_exit:
                interfaces["exit"]["events"] = self.E_exit
        
        if self.Sigma_run or self.E_run:
            interfaces["run"] = {}
            if self.Sigma_run:
                interfaces["run"]["vars"] = self.Sigma_run
            if self.E_run:
                interfaces["run"]["events"] = self.E_run
        
        if self.Sigma_int or self.Sigma_func or self.E_int:
            interfaces["internal"] = {}
            if self.Sigma_int:
                interfaces["internal"]["controlVars"] = self.Sigma_int
            if self.Sigma_func:
                interfaces["internal"]["functionVars"] = self.Sigma_func
            if self.E_int:
                interfaces["internal"]["events"] = self.E_int
        
        if self.Sigma_out or self.E_out:
            interfaces["output"] = {}
            if self.Sigma_out:
                interfaces["output"]["vars"] = self.Sigma_out
            if self.E_out:
                interfaces["output"]["events"] = self.E_out
        
        if interfaces:
            d["interfaces"] = interfaces
        return d
    
    def get_all_outputs(self) -> Set[str]:
        """获取模式的所有输出（变量+事件）"""
        return set(self.Sigma_out) | set(self.E_out)
    
    def get_effective_inputs(self) -> Tuple[Set[str], Set[str]]:
        """
        获取有效输入
        
        论文定义：V_in(u) = Σ_entry ∪ Σ_exit ∪ Σ_run
                 E_in(u) = E_entry ∪ E_exit ∪ E_run
        """
        vars_set = set(self.Sigma_entry) | set(self.Sigma_exit) | set(self.Sigma_run)
        events_set = set(self.E_entry) | set(self.E_exit) | set(self.E_run)
        return vars_set, events_set
    
    def get_effective_encapsulated(self) -> Tuple[Set[str], Set[str]]:
        """
        获取有效封装
        
        论文定义：V_enc(u) = Σ_int ∪ Σ_func
                 E_enc(u) = E_int
        """
        vars_set = set(self.Sigma_int) | set(self.Sigma_func)
        events_set = set(self.E_int)
        return vars_set, events_set


# ============================================================================
# 结构关系 R - 四元组
# ============================================================================

@dataclass
class Relation:
    """
    结构关系 r ∈ R
    
    论文定义：r = ⟨relType, e1, e2, params⟩
    
    用于表达模块包含子模块、模块拥有模式、模式包含子模式等结构关系
    """
    relType: str                                 # 关系类型标识符
    e1: str                                      # 源元素名称（模块或模式）
    e2: str                                      # 目标元素名称（模块或模式）
    params: Dict[str, Any] = field(default_factory=dict)  # 关系参数
    name: str = ""                               # 可选的名称
    
    def to_dict(self) -> dict:
        """
        遵循论文定义（3.2.2节）：
        r = ⟨relType, e1, e2, params⟩
        """
        d = {
            "relType": self.relType,
            "e1": self.e1,
            "e2": self.e2
        }
        if self.name:
            d["name"] = self.name
        if self.params:
            d["params"] = self.params
        return d


# ============================================================================
# 过程 P - 八元组
# ============================================================================

@dataclass
class StatePredicate:
    """
    状态特征谓词
    
    论文定义：K_p: L_p → Expr
    - L_p 是状态标签集合（助记符，不存储数据）
    - K_p(l) 是定义在 Σ_read 上的布尔表达式
    - 系统处于状态 l 当且仅当 K_p(l) 为真
    """
    label: str                                   # l ∈ L_p - 状态标签
    predicate: str                               # K_p(l) - 特征谓词表达式
    
    def to_dict(self) -> dict:
        return {"label": self.label, "predicate": self.predicate}


@dataclass
class TransitionAction:
    """
    变量更新动作
    
    论文定义：α = { (v, φ) | v ∈ Σ_write_p, φ ∈ Expr }
    表示迁移发生时，变量 v 的下一时刻取值被更新为表达式 φ 的计算结果
    """
    var: str                                     # v - 目标变量
    expr: str                                    # φ - 表达式
    
    def to_dict(self) -> dict:
        return {"var": self.var, "expr": self.expr}


@dataclass
class Transition:
    """
    迁移 t ∈ T_p
    
    论文定义：t = ⟨l_src, γ, E_tr, α, l_dst⟩
    
    其中：
    - l_src ∈ L_p: 源状态标签
    - γ ∈ Expr: 额外的卫士条件
    - E_tr ⊆ E_trig_p: 触发事件集合
    - α: 变量更新动作集合
    - l_dst ∈ L_p: 目标状态标签（语义断言）
    """
    source: str                                  # l_src - 源状态标签
    target: str                                  # l_dst - 目标状态标签
    guard: Optional[str] = None                  # γ - 卫士条件
    triggerEvents: List[str] = field(default_factory=list)  # E_tr - 触发事件
    actions: List[TransitionAction] = field(default_factory=list)  # α - 动作集合
    
    def to_dict(self) -> dict:
        d = {"source": self.source, "target": self.target}
        if self.guard:
            d["guard"] = self.guard
        if self.triggerEvents:
            d["triggerEvents"] = self.triggerEvents
        if self.actions:
            d["actions"] = [a.to_dict() for a in self.actions]
        return d


@dataclass
class Process:
    """
    过程 p ∈ P
    
    论文定义：p = ⟨name_p, owner_p, Σ_read, Σ_write, E_trig, L_p, K_p, T_p⟩
    
    语义约束：
    - Σ_read ⊆ V_in ∪ V_enc ∪ V_out（可读取所有可见变量）
    - Σ_write ⊆ V_enc ∪ V_out（严禁修改输入）
    - 状态互斥：∀i≠j: K_p(l_i) ∧ K_p(l_j) ⊢ ⊥
    - 动作与目标状态一致性：执行 α 后必须满足 K_p(l_dst)
    """
    name: str                                    # name_p - 唯一标识符
    owner: str                                   # owner_p ∈ M_o ∪ M_d
    
    # 读写集
    Sigma_read: List[str] = field(default_factory=list)   # Σ_read - 读变量
    Sigma_write: List[str] = field(default_factory=list)  # Σ_write - 写变量
    E_trig: List[str] = field(default_factory=list)       # E_trig - 触发事件
    
    # 状态与谓词（L_p + K_p）
    state_predicates: List[StatePredicate] = field(default_factory=list)
    
    # 迁移
    transitions: List[Transition] = field(default_factory=list)  # T_p
    
    def get_state_labels(self) -> List[str]:
        """获取 L_p - 状态标签集合"""
        return [sp.label for sp in self.state_predicates]
    
    def get_predicate(self, label: str) -> Optional[str]:
        """获取 K_p(label) - 状态特征谓词"""
        for sp in self.state_predicates:
            if sp.label == label:
                return sp.predicate
        return None
    
    def get_K_p(self) -> Dict[str, str]:
        """获取完整的 K_p 映射"""
        return {sp.label: sp.predicate for sp in self.state_predicates}
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "owner": self.owner,
            "sets": {
                "read": self.Sigma_read,
                "write": self.Sigma_write,
                "trigger": self.E_trig
            },
            "states": self.get_state_labels(),
            "statePredicates": self.get_K_p(),
            "transitions": [t.to_dict() for t in self.transitions]
        }
    
    def validate(self, system: 'MTRDLSystem') -> List[str]:
        """验证过程的合法性"""
        errors = []
        
        # 1. 解析属主
        owner_obj = system.resolve_owner(self.owner)
        if not owner_obj:
            errors.append(f"Process '{self.name}': owner '{self.owner}' not found")
            return errors
        
        # 2. 获取属主的有效变量和事件
        V_in, E_in = owner_obj.get_effective_inputs()
        V_enc, E_enc = owner_obj.get_effective_encapsulated()
        V_out = owner_obj.get_all_outputs()
        
        all_vars = V_in | V_enc | V_out
        all_events = E_in | E_enc
        
        # 3. 读变量权限检查
        for var in self.Sigma_read:
            if var not in all_vars:
                errors.append(f"Process '{self.name}': read variable '{var}' not visible")
        
        # 4. 写变量权限检查（严禁修改输入）
        for var in self.Sigma_write:
            if var in V_in:
                errors.append(f"Process '{self.name}': cannot write to input variable '{var}'")
            if var not in (V_enc | V_out):
                errors.append(f"Process '{self.name}': write variable '{var}' not in encapsulated/output")
        
        # 5. 触发事件检查
        for evt in self.E_trig:
            if evt not in all_events:
                errors.append(f"Process '{self.name}': trigger event '{evt}' not visible")
        
        # 6. 状态标签和谓词一致性
        labels = set(self.get_state_labels())
        pred_keys = set(self.get_K_p().keys())
        if labels != pred_keys:
            errors.append(f"Process '{self.name}': state labels and predicates mismatch")
        
        # 7. 迁移引用的状态检查
        for t in self.transitions:
            if t.source not in labels:
                errors.append(f"Process '{self.name}': source state '{t.source}' not in L_p")
            if t.target not in labels:
                errors.append(f"Process '{self.name}': target state '{t.target}' not in L_p")
            for evt in t.triggerEvents:
                if evt not in self.E_trig:
                    errors.append(f"Process '{self.name}': trigger event '{evt}' not in E_trig")
        
        return errors


# ============================================================================
# 通信 C - 五元组
# ============================================================================

@dataclass
class Communication:
    """
    通信 c ∈ C
    
    论文定义：c = ⟨name_c, sender, receiver, type_c, content_c⟩
    
    约束：
    - sender, receiver ∈ M_o ∪ M_d
    - type_c ∈ {DATAFLOW, EVENT}
    - content_c ⊆ sender.outputs
    """
    name: str                                    # name_c - 唯一标识符
    sender: str                                  # 发送方（模块名或模式名）
    receiver: str                                # 接收方（模块名或模式名）
    type: CommunicationType = CommunicationType.DATAFLOW  # type_c
    content: List[str] = field(default_factory=list)      # content_c
    
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "sender": self.sender,
            "receiver": self.receiver,
            "type": self.type.value,
            "content": self.content
        }
    
    def validate(self, system: 'MTRDLSystem') -> List[str]:
        """验证通信的合法性"""
        errors = []
        
        sender_obj = system.resolve_owner(self.sender)
        receiver_obj = system.resolve_owner(self.receiver)
        
        if not sender_obj:
            errors.append(f"Communication '{self.name}': sender '{self.sender}' not found")
        if not receiver_obj:
            errors.append(f"Communication '{self.name}': receiver '{self.receiver}' not found")
        
        if sender_obj:
            sender_outputs = sender_obj.get_all_outputs()
            for item in self.content:
                if item not in sender_outputs:
                    errors.append(f"Communication '{self.name}': '{item}' not in {self.sender}.outputs")
        
        return errors


# ============================================================================
# 推导规则 F
# ============================================================================

@dataclass
class DerivationRule:
    """
    推导规则 f ∈ F
    
    论文描述：定义系统内属性的计算逻辑（根据变量 A 计算变量 B 的生成规则）
    
    推断定义：f = ⟨owner_f, target_f, expr_f, cond_f⟩
    - owner_f ∈ M_o ∪ M_d: 规则所属的结构构件
    - target_f ∈ Σ_all: 目标变量
    - expr_f ∈ Expr: 计算表达式
    - cond_f ∈ Expr: 可选的前置条件
    """
    owner: str                                   # owner_f - 所属元素
    target: str                                  # target_f - 目标变量
    expression: str                              # expr_f - 计算表达式
    condition: Optional[str] = None              # cond_f - 前置条件
    name: str = ""                               # 可选的名称
    
    def to_dict(self) -> dict:
        d = {
            "owner": self.owner,
            "target": self.target,
            "expression": self.expression
        }
        if self.name:
            d["name"] = self.name
        if self.condition:
            d["condition"] = self.condition
        return d
    
    def validate(self, system: 'MTRDLSystem') -> List[str]:
        """验证推导规则的合法性"""
        errors = []
        
        # 检查属主存在
        owner_obj = system.resolve_owner(self.owner)
        if not owner_obj:
            errors.append(f"DerivationRule '{self.name or self.target}': owner '{self.owner}' not found")
        
        # 检查目标变量存在
        target_var = system.dataDictionary.get_variable(self.target)
        if not target_var:
            errors.append(f"DerivationRule '{self.name or self.target}': target '{self.target}' not in Σ_all")
        
        return errors


# ============================================================================
# 约束 Φ
# ============================================================================

@dataclass
class Constraint:
    """
    约束 φ ∈ Φ
    
    论文描述：定义系统的合法边界与公理（对系统状态空间的裁剪与限制）
    
    推断定义：φ = ⟨name_φ, scope_φ, expr_φ, kind_φ⟩
    - name_φ: 约束标识符
    - scope_φ ∈ M_o ∪ M_d ∪ {global}: 作用域
    - expr_φ ∈ Expr: 约束表达式
    - kind_φ ∈ {invariant, assumption, guarantee}: 约束类型
    """
    name: str = ""                               # name_φ - 约束标识符
    scope: str = "global"                        # scope_φ - 作用域
    expression: str = ""                         # expr_φ - 约束表达式
    kind: ConstraintKind = ConstraintKind.INVARIANT  # kind_φ - 约束类型
    description: str = ""                        # 描述
    
    def to_dict(self) -> dict:
        d = {
            "scope": self.scope,
            "expression": self.expression,
            "kind": self.kind.value
        }
        if self.name:
            d["name"] = self.name
        if self.description:
            d["description"] = self.description
        return d
    
    def validate(self, system: 'MTRDLSystem') -> List[str]:
        """验证约束的合法性"""
        errors = []
        
        if self.scope != "global":
            owner_obj = system.resolve_owner(self.scope)
            if not owner_obj:
                errors.append(f"Constraint '{self.name}': scope '{self.scope}' not found")
        
        return errors


# ============================================================================
# 完整系统模型 S
# ============================================================================

@dataclass
class MTRDLSystem:
    """
    模式转换系统 S
    
    论文定义：S = ⟨M_o, M_d, R, P, C, D, F, Φ⟩
    """
    name: str = ""
    version: str = "1.0"
    description: str = ""
    
    # 八元组
    dataDictionary: DataDictionary = field(default_factory=DataDictionary)  # D
    modules: List[Module] = field(default_factory=list)                     # M_o
    modes: List[Mode] = field(default_factory=list)                         # M_d
    relations: List[Relation] = field(default_factory=list)                 # R
    processes: List[Process] = field(default_factory=list)                  # P
    communications: List[Communication] = field(default_factory=list)       # C
    derivation_rules: List[DerivationRule] = field(default_factory=list)    # F
    constraints: List[Constraint] = field(default_factory=list)             # Φ
    
    # 索引（运行时构建）
    _module_index: Dict[str, Module] = field(default_factory=dict, repr=False)
    _mode_index: Dict[str, Mode] = field(default_factory=dict, repr=False)
    _children_map: Dict[str, List[str]] = field(default_factory=dict, repr=False)
    _mode_owner_map: Dict[str, str] = field(default_factory=dict, repr=False)
    
    def build_index(self):
        """构建所有索引"""
        self.dataDictionary.build_index()
        self._module_index = {m.name: m for m in self.modules}
        self._mode_index = {m.name: m for m in self.modes}
        self._children_map = defaultdict(list)
        self._mode_owner_map = {}
        
        for r in self.relations:
            if r.relType == "CONTAINS" or r.relType == "contains":
                self._children_map[r.e1].append(r.e2)
            elif r.relType == "OWNS" or r.relType == "owns":
                self._mode_owner_map[r.e2] = r.e1
    
    def get_module(self, name: str) -> Optional[Module]:
        return self._module_index.get(name)
    
    def get_mode(self, name: str) -> Optional[Mode]:
        return self._mode_index.get(name)
    
    def resolve_owner(self, owner_name: str) -> Optional[Union[Module, Mode]]:
        """解析属主（模块或模式）"""
        if owner_name in self._module_index:
            return self._module_index[owner_name]
        if owner_name in self._mode_index:
            return self._mode_index[owner_name]
        return None
    
    def get_submodules(self, module_name: str) -> List[str]:
        return self._children_map.get(module_name, [])
    
    def get_module_modes(self, module_name: str) -> List[str]:
        return [m for m, owner in self._mode_owner_map.items() if owner == module_name]
    
    def get_mode_owner(self, mode_name: str) -> Optional[str]:
        return self._mode_owner_map.get(mode_name)
    
    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "version": self.version
        }
        if self.description:
            d["description"] = self.description
        
        dd = self.dataDictionary.to_dict()
        if dd:
            d["dataDictionary"] = dd
        
        if self.modules:
            d["modules"] = [m.to_dict() for m in self.modules]
        
        if self.modes:
            d["modes"] = [m.to_dict() for m in self.modes]
        
        if self.relations:
            d["relations"] = [r.to_dict() for r in self.relations]
        
        if self.processes:
            d["processes"] = [p.to_dict() for p in self.processes]
        
        if self.communications:
            d["communications"] = [c.to_dict() for c in self.communications]
        
        if self.derivation_rules:
            d["derivation_rules"] = [r.to_dict() for r in self.derivation_rules]
        
        if self.constraints:
            d["constraints"] = [c.to_dict() for c in self.constraints]
        
        return d
    
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
    
    def save(self, filepath: str) -> None:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(self.to_json())
        print(f"Saved to: {filepath}")
    
    def validate(self) -> List[str]:
        """验证整个系统的合法性"""
        errors = []
        
        # 验证数据字典
        var_names = {v.name for v in self.dataDictionary.variables}
        event_names = {e.name for e in self.dataDictionary.events}
        overlap = var_names & event_names
        if overlap:
            errors.append(f"Variable and event names overlap: {overlap}")
        
        # 验证过程
        for p in self.processes:
            errors.extend(p.validate(self))
        
        # 验证通信
        for c in self.communications:
            errors.extend(c.validate(self))
        
        # 验证推导规则
        for r in self.derivation_rules:
            errors.extend(r.validate(self))
        
        # 验证约束
        for c in self.constraints:
            errors.extend(c.validate(self))
        
        return errors


# ============================================================================
# 创建论文示例
# ============================================================================

def create_paper_example() -> MTRDLSystem:
    """创建论文 3.2 节的示例模型"""
    system = MTRDLSystem(name="FMS_ASEL_Model", version="1.0")
    
    # 数据字典
    system.dataDictionary = DataDictionary(
        types=[
            TypeDefinition(name="Altitude_Ft", kind="Int", min=-1000, max=50000),
            TypeDefinition(name="VertSpeed_FPM", kind="Int", min=-6000, max=6000),
        ],
        variables=[
            Variable(name="altitude", type="Altitude_Ft", initial_value=10000,
                    attributes={"stereotype": "Monitor"}),
            Variable(name="ASEL_Law_status", type="boolean", initial_value=False,
                    attributes={"stereotype": "Control"}),
            Variable(name="is_ASEL_active", type="boolean", initial_value=False,
                    attributes={"stereotype": "state"}),
            Variable(name="is_ASEL_armed", type="boolean", initial_value=False,
                    attributes={"stereotype": "state"}),
            Variable(name="fmcp_target_alt", type="Altitude_Ft", initial_value=0,
                    attributes={"stereotype": "Monitor"}),
            Variable(name="vert_speed", type="VertSpeed_FPM", initial_value=0,
                    attributes={"stereotype": "Monitor"}),
        ],
        events=[
            Event(
                name="ev_capture_condition_met",
                kind=EventKind.DERIVED,
                derivation=EventDerivation(
                    vars=["fmcp_target_alt", "altitude"],
                    expression="abs(fmcp_target_alt - altitude) < 900"
                )
            ),
            Event(
                name="ev_capture_complete",
                kind=EventKind.DERIVED,
                derivation=EventDerivation(
                    vars=["fmcp_target_alt", "altitude"],
                    expression="abs(fmcp_target_alt - altitude) < 50"
                )
            ),
        ]
    )
    
    # 模块
    system.modules = [
        Module(
            name="fmcp_Panel",
            Sigma_out=["fmcp_target_alt"]
        )
    ]
    
    # 模式
    system.modes = [
        Mode(
            name="ASEL_mode",
            Sigma_entry=["altitude", "fmcp_target_alt", "is_ASEL_armed"],
            E_entry=["ev_capture_condition_met"],
            Sigma_exit=["altitude", "fmcp_target_alt", "vert_speed"],
            E_exit=["ev_capture_complete"],
            Sigma_out=["is_ASEL_active", "ASEL_Law_status"]
        )
    ]
    
    # 关系
    system.relations = [
        Relation(relType="OWNS", e1="fmcp_Panel", e2="ASEL_mode")
    ]
    
    # 过程
    system.processes = [
        Process(
            name="Proc_Cap_Logic",
            owner="ASEL_mode",
            Sigma_read=["is_ASEL_armed"],
            Sigma_write=["is_ASEL_active"],
            E_trig=["ev_capture_condition_met", "ev_capture_complete"],
            state_predicates=[
                StatePredicate(label="S_IDLE", predicate="is_ASEL_active == false"),
                StatePredicate(label="S_CAPTURING", predicate="is_ASEL_active == true"),
            ],
            transitions=[
                Transition(
                    source="S_IDLE",
                    target="S_CAPTURING",
                    triggerEvents=["ev_capture_condition_met"],
                    guard="is_ASEL_armed == true",
                    actions=[TransitionAction(var="is_ASEL_active", expr="true")]
                ),
                Transition(
                    source="S_CAPTURING",
                    target="S_IDLE",
                    triggerEvents=["ev_capture_complete"],
                    actions=[TransitionAction(var="is_ASEL_active", expr="false")]
                ),
            ]
        )
    ]
    
    # 推导规则
    system.derivation_rules = [
        DerivationRule(
            owner="ASEL_mode",
            target="ASEL_Law_status",
            expression="is_ASEL_armed == true"
        )
    ]
    
    system.build_index()
    return system


# ============================================================================
# 使用示例
# ============================================================================

if __name__ == "__main__":
    system = create_paper_example()
    
    # 验证
    errors = system.validate()
    if errors:
        print("Validation errors:")
        for err in errors:
            print(f"  - {err}")
    else:
        print("Validation passed!")
    
    # 输出 JSON
    print("\n" + "=" * 60)
    print("MTRDL System JSON:")
    print("=" * 60)
    print(system.to_json())
    
    # 保存
    system.save("mtrdl_system.json")