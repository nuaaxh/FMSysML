import os
import pandas as pd


def guard_csv_to_nusmv(csv_path: str) -> str:
    """
    将 guard CSV 转换为 NuSMV 条件表达式。
    如果 CSV 文件不存在或为空，返回 TRUE。
    """
    if not os.path.exists(csv_path):
        print(f"Warning: {csv_path} not found. Using TRUE as guard.")
        return "TRUE"

    if os.path.getsize(csv_path) == 0:
        print(f"Warning: {csv_path} is empty. Using TRUE as guard.")
        return "TRUE"

    try:
        df = pd.read_csv(csv_path, dtype=str).fillna("*")
    except pd.errors.EmptyDataError:
        print(f"Warning: {csv_path} is empty or malformed. Using TRUE as guard.")
        return "TRUE"

    conditions = []
    for _, row in df.iterrows():
        row_cond = []
        for col in df.columns:
            val = str(row[col]).strip().upper()
            if val == "TRUE":
                row_cond.append(col)
            elif val == "FALSE":
                row_cond.append(f"!{col}")
        if row_cond:
            conditions.append(" & ".join(row_cond))

    if not conditions:
        return "TRUE"

    return " | ".join(f"({c})" for c in conditions)
