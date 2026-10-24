"""示例与上传数据共用的历史回测计算。输入只来自当前会话的数据框。"""

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error


FEATURES = [
    "hour", "weekday", "month", "is_weekend", "lag_24", "lag_48",
    "lag_168", "rolling_24", "rolling_168"
]
MIN_DAYS = 45


def build_features(series):
    # 保留原模型的特征定义：目标时刻只使用 24 小时前及更早的负荷。
    past = series.ffill(limit=2)
    x = pd.DataFrame(index=series.index)
    x["hour"] = x.index.hour
    x["weekday"] = x.index.dayofweek
    x["month"] = x.index.month
    x["is_weekend"] = (x.index.dayofweek >= 5).astype(int)
    x["lag_24"] = past.shift(24)
    x["lag_48"] = past.shift(48)
    x["lag_168"] = past.shift(168)
    x["rolling_24"] = past.rolling(24, min_periods=20).mean().shift(24)
    x["rolling_168"] = past.rolling(168, min_periods=140).mean().shift(24)
    x["actual"] = series
    return x.dropna()


def calculate_anomaly_threshold(series, times, history_end):
    history = series.loc[series.index < history_end].dropna()
    info = pd.DataFrame({
        "load": history.to_numpy(),
        "weekday": history.index.dayofweek,
        "hour": history.index.hour,
    })
    grouped = info.groupby(["weekday", "hour"])["load"]
    quantiles = grouped.quantile(0.95)
    counts = grouped.size()
    return np.array([
        quantiles.get((t.dayofweek, t.hour), np.nan)
        if counts.get((t.dayofweek, t.hour), 0) >= 3 else np.nan
        for t in times
    ], dtype=float)


def peak_shaving_scenario(load, percentage):
    # 与原示例算法一致：总量不变，按低负荷时段依次分配。
    original = np.asarray(load, dtype=float)
    if original.size == 0:
        return original.copy(), 0.0, 0.0
    if not np.isfinite(original).all():
        raise ValueError("负荷包含缺失或非有限值，无法计算错峰情景")
    peak = float(np.max(original))
    average = float(np.mean(original))
    if peak <= 0:
        return original.copy(), 0.0, 0.0
    cap = max(peak * (1 - percentage / 100), average)
    adjusted = np.minimum(original, cap)
    energy_to_shift = float(np.sum(original - adjusted))
    remaining = energy_to_shift
    for idx in np.argsort(adjusted):
        addition = min(max(cap - adjusted[idx], 0), remaining)
        adjusted[idx] += addition
        remaining -= addition
        if remaining < 1e-8:
            break
    reduction = (1 - np.max(adjusted) / peak) * 100
    return adjusted, energy_to_shift, reduction


def analyze_building(series):
    """返回 (结果, 不足原因)；不在全局缓存上传数据或模型。"""
    series = series.sort_index().astype(float)
    span_days = (series.index.max().normalize() - series.index.min().normalize()).days + 1
    valid_hours = int(series.notna().sum())
    if span_days < MIN_DAYS:
        return None, f"当前覆盖 {span_days} 天，历史回测至少需要 {MIN_DAYS} 天整点数据，还差 {MIN_DAYS - span_days} 天。"
    if valid_hours < MIN_DAYS * 20:
        return None, f"当前有 {valid_hours} 个有效小时，至少需要 {MIN_DAYS * 20} 个，还差 {MIN_DAYS * 20 - valid_hours} 个。"

    dataset = build_features(series)
    days = pd.Index(sorted(set(dataset.index.normalize())))
    if len(days) < 30:
        return None, f"生成历史特征后仅有 {len(days)} 个有效日期，至少需要 30 天。"
    train_end = days[int(len(days) * 0.60)]
    test_start = days[int(len(days) * 0.80)]
    train = dataset.loc[dataset.index < train_end]
    validation = dataset.loc[(dataset.index >= train_end) & (dataset.index < test_start)]
    test = dataset.loc[dataset.index >= test_start].copy()
    complete_days = [day.date() for day, count in test.groupby(test.index.normalize()).size().items() if count == 24]
    if min(len(train), len(validation), len(test)) < 24 * 4 or not complete_days:
        return None, "连续有效记录不足，无法形成训练、验证和完整的 24 小时回测日期。"

    model = HistGradientBoostingRegressor(
        max_iter=150, max_leaf_nodes=15, learning_rate=0.06,
        l2_regularization=3.0, random_state=42
    )
    model.fit(train[FEATURES], train["actual"])
    val_prediction = np.maximum(model.predict(validation[FEATURES]), 0)
    test["prediction"] = np.maximum(model.predict(test[FEATURES]), 0)
    test["baseline"] = test["lag_168"]
    metrics = {
        "mae": mean_absolute_error(test["actual"], test["prediction"]),
        "rmse": np.sqrt(mean_squared_error(test["actual"], test["prediction"])),
        "baseline_mae": mean_absolute_error(test["actual"], test["baseline"]),
        "validation_mae": mean_absolute_error(validation["actual"], val_prediction),
        "validation_baseline_mae": mean_absolute_error(validation["actual"], validation["lag_168"]),
    }
    method = "模型预测" if metrics["validation_mae"] < metrics["validation_baseline_mae"] else "历史同期基线"
    test["selected_prediction"] = test["prediction"] if method == "模型预测" else test["baseline"]
    return {
        "test": test,
        "metrics": metrics,
        "method": method,
        "days": complete_days,
        "train_end": train_end,
        "test_start": test_start,
    }, None
