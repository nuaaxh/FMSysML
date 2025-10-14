import re
import pandas as pd
import os

# ========= 配置输入输出文件路径 =========
input_file = r"D:\OneDrive\桌面\EA模型转换\需求处理\Req.xlsx"  # CSV 或 Excel
output_dir = r"D:\OneDrive\桌面\EA模型转换\guards_csv"  # 每个需求的 guard CSV 存放目录

# ========= Guard 清洗函数 =========
def clean_condition(cond):
    cond = cond.strip()
    cond = cond.lower()
    cond = cond.replace("true", "TRUE").replace("false", "FALSE")
    cond = re.sub(r"[^a-zA-Z0-9_]", "_", cond)  # 非字母数字替换成 _
    cond = re.sub(r"_+", "_", cond)  # 连续下划线合并
    cond = cond.strip("_")

    # 去掉开头的 "the"
    cond = re.sub(r"^the_", "", cond)
    cond = re.sub(r"^the", "", cond)

    # 下划线转空格
    cond = cond.replace("_", " ").strip()
    return cond

# ========= WIWT 预处理 =========
def preprocess_wiwt(req_text):
    text = str(req_text)
    text = re.sub(r"(?is)\bwhile\b.*?\bif\b", "If", text)
    text = re.sub(r"(?is)\bwhen\b.*?\bthen\b", "Then", text)
    return text.strip()

# ========= If-Then 需求解析 =========
def parse_if_then(req_text):
    req_text = str(req_text).strip()
    req_text = re.sub(r"[,;]", " ", req_text)
    req_text = re.sub(r"\s+", " ", req_text).strip()

    match = re.search(r"(?i)^if (.+?) then (.+)$", req_text)
    if not match:
        return []  # 改成返回空，而不是报错

    if_part = match.group(1).strip()

    groups = re.split(r"\bor\s+if\b|\bor\b", if_part, flags=re.IGNORECASE)
    groups = [grp.strip() for grp in groups if grp.strip()]

    parsed_groups = []
    for group in groups:
        conditions = [clean_condition(cond) for cond in re.split(r"\band\b", group, flags=re.IGNORECASE)]
        conditions = list(dict.fromkeys(conditions))
        parsed_groups.append(conditions)

    # 去重
    unique_groups = []
    seen = set()
    for group in parsed_groups:
        key = tuple(sorted(group))
        if key not in seen:
            seen.add(key)
            unique_groups.append(group)

    return unique_groups

# ========= WIWT 需求解析 =========
def parse_wiwt(req_text):
    req_text = str(req_text).strip()
    req_text = re.sub(r"[,;]", " ", req_text)
    req_text = re.sub(r"\s+", " ", req_text).strip()

    match = re.search(r"(?i)\bif (.+?) then (.+)$", req_text)
    if not match:
        return []

    if_part = match.group(1).strip()

    groups = re.split(r"\bor\s+if\b|\bor\b", if_part, flags=re.IGNORECASE)
    groups = [grp.strip() for grp in groups if grp.strip()]

    parsed_groups = []
    for group in groups:
        conditions = [clean_condition(cond) for cond in re.split(r"\band\b", group, flags=re.IGNORECASE)]
        conditions = list(dict.fromkeys(conditions))
        parsed_groups.append(conditions)

    unique_groups = []
    seen = set()
    for group in parsed_groups:
        key = tuple(sorted(group))
        if key not in seen:
            seen.add(key)
            unique_groups.append(group)

    return unique_groups

# ========= 批量处理 =========
def process_file(input_path, output_path):
    if input_path.lower().endswith(".csv"):
        df = pd.read_csv(input_path)
    else:
        df = pd.read_excel(input_path)

    if "requirement" not in df.columns or "ID" not in df.columns:
        raise ValueError("输入文件必须包含 'ID' 列 和 'requirement' 列")

    os.makedirs(output_path, exist_ok=True)

    for _, row in df.iterrows():
        req_id = row["ID"]
        req_text = str(row["requirement"])

        # 判断模板类型（含 while/when 的当作 WIWT）
        if re.search(r"(?i)\bwhile\b|\bwhen\b", req_text):
            groups = parse_wiwt(preprocess_wiwt(req_text))
        else:
            groups = parse_if_then(req_text)

        # 收集所有出现过的变量
        all_conditions = sorted(set(c for g in groups for c in g)) if groups else []

        table = []
        if groups:
            # 构造 truth table 风格
            for group in groups:
                row_vals = []
                for cond in all_conditions:
                    if cond in group:
                        row_vals.append("TRUE")  # 1 -> TRUE
                    else:
                        row_vals.append("*")  # * 表示无关
                table.append(row_vals)
            df_out = pd.DataFrame(table, columns=all_conditions)
        else:
            # 没有 guard，也要生成文件
            df_out = pd.DataFrame([[]])  # 空表

        file_name = os.path.join(output_path, f"Req_{req_id}_guard.csv")
        df_out.to_csv(file_name, index=False, encoding="utf-8-sig")
        print(f"需求 {req_id} 已保存到 {file_name}")

    print("全部处理完成！")

# ========= 主程序入口 =========
if __name__ == "__main__":
    process_file(input_file, output_dir)
