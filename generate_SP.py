import json
import os

def generate_nuxmv_properties(mtrdl_json_path):
    """
    根据 MTRDL 规范中的模式/模块关系自动生成 NuXMV 安全性质规约
    """
    if not os.path.exists(mtrdl_json_path):
        print(f"Error: File {mtrdl_json_path} not found.")
        return []

    with open(mtrdl_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    variables = data.get("dataDictionary", {}).get("variables", [])
    relations = data.get("relations", [])

    # 1. 建立模式/模块名称到其激活状态变量名的映射 (Stereotype == State)
    owner_to_var = {}
    for var in variables:
        attrs = var.get("attributes", {})
        owner = attrs.get("owner")
        stereotype = attrs.get("stereotype")
        
        if owner and stereotype == "State":
            v_name = var["name"]
            # 过滤掉显式表示反面的变量（如带有 inactive 尾缀的变量），保留核心激活变量
            if "inactive" in v_name.lower():
                continue
            # 优先选择包含 active 或 armed 的主状态变量
            if owner not in owner_to_var or "active" in v_name.lower() or "armed" in v_name.lower():
                owner_to_var[owner] = v_name

    generated_specs = []
    # 用于记录 Refine 关系，以便后续生成子模式间的互斥性质 (sum(sub_modes) <= 1)
    refinement_map = {} 

    # 2. 遍历关系列表，根据论文 4.1.2 & 4.3.2节 的语义规则生成性质
    for rel in relations:
        rel_type = rel.get("relType")
        e1 = rel.get("e1")
        e2 = rel.get("e2")
        rel_name = rel.get("name", f"{rel_type}_{e1}_{e2}")

        var1 = owner_to_var.get(e1)
        var2 = owner_to_var.get(e2)

        # 如果关系链中的元素未关联状态变量（例如属于外部大模块而非具体模式），则跳过
        if not var1 or not var2:
            continue

        # 规则 1: Exclude 关系 (m_i & m_j = 0)
        if rel_type == "exclude":
            spec = f"LTLSPEC G !({var1} & {var2});"
            comment = f"-- Property: {rel_name} (Exclude Relation)"
            generated_specs.append((comment, spec))

        # 规则 2: Inhibit 或 arm_of 关系 (m_i = 1 -> m_j = 0)
        elif rel_type in ["inhibit", "arm_of", "armmode"]:
            spec = f"LTLSPEC G ({var1} -> !{var2});"
            comment = f"-- Property: {rel_name} ({rel_type.capitalize()} Relation)"
            generated_specs.append((comment, spec))

        # 规则 3: Depend 关系 (m_i = 1 -> m_j = 1)
        elif rel_type == "depend":
            spec = f"LTLSPEC G ({var1} -> {var2});"
            comment = f"-- Property: {rel_name} (Depend Relation)"
            generated_specs.append((comment, spec))

        # 规则 4: Refine 关系 (包含两部分语义: 1. 子模式激活则父模式必激活; 2. 同一父模式下的子模式互斥)
        elif rel_type == "refine":
            # 根据论文定义：e1 是子模式，e2 是父模式 (e1 refines e2)
            # 语义A: m_sub -> m_super
            spec = f"LTLSPEC G ({var1} -> {var2});"
            comment = f"-- Property: {rel_name} (Refine Sub-to-Super Dependency)"
            generated_specs.append((comment, spec))

            # 收集同一父模式下的所有子模式，用于后续生成互斥属性
            if e2 not in refinement_map:
                refinement_map[e2] = []
            if var1 not in refinement_map[e2]:
                refinement_map[e2].append(var1)

    # 规则 5: 处理 Refine 的互斥语义 (sum(m_sub) <= 1)
    for super_mode, sub_vars in refinement_map.items():
        if len(sub_vars) > 1:
            for i in range(len(sub_vars)):
                for j in range(i + 1, len(sub_vars)):
                    spec = f"LTLSPEC G !({sub_vars[i]} & {sub_vars[j]});"
                    comment = f"-- Property: refine_mutex_{super_mode}_{i}_{j} (Submodes of {super_mode} are mutually exclusive)"
                    generated_specs.append((comment, spec))

    return generated_specs

# --- 测试运行 ---
if __name__ == "__main__":
    # 假设将你提供的示例 JSON 数据保存为 'mtrdl_model.json'
    json_filename = "mtrdl_output.json"



    print("=== 开始从 MTRDL 自动生成安全性质 ===")
    specs = generate_nuxmv_properties(json_filename)
    
    for comment, spec in specs:
        print(comment)
        print(spec)
    print("=== 性质生成完毕 ===")