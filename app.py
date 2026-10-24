"""智碳管家：会话内建筑小时负荷分析。"""

from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analysis_engine import analyze_building, calculate_anomaly_threshold, peak_shaving_scenario
from data_input import DataInputError, normalize_data, read_upload, template_csv
from report_pdf import build_pdf_report, build_quality_pdf


ROOT = Path(__file__).resolve().parent
SAMPLE_PATH = ROOT / "data" / "electricity_sample_5buildings.csv"
st.set_page_config(page_title="智碳管家 | 建筑智慧用能", page_icon="⚡", layout="wide")
st.html("""
<style>
.stApp { background: #F6F8FC; color: #17385F; }
[data-testid="stMainBlockContainer"] { padding-top: 4.5rem; padding-bottom: 2rem; max-width: 1450px; }
h1, h2, h3 { color: #17385F; }
div[data-testid="stMetric"] { background: #FFF; padding: 18px 20px; border-radius: 12px;
    border: 1px solid #E3E9EF; box-shadow: 0 3px 14px rgba(23,56,95,.05); }
.zc-header { background: #FFF; padding: 18px 24px; border-radius: 12px;
    border: 1px solid #E3E9EF; margin-bottom: 1rem; }
.zc-brand { font-size: 1.6rem; font-weight: 700; color: #17385F; }
.zc-subtitle { color: #64748B; font-size: .93rem; }
</style>
""")
st.markdown("""
<div class="zc-header"><div class="zc-brand">⚡ 智碳管家</div>
<div class="zc-subtitle">建筑智慧用能分析与决策平台</div></div>
""", unsafe_allow_html=True)


def switch_source(mode):
    for key in ("active_df", "quality", "source_name", "source_token", "analysis_cache",
                "analysis_cache_key", "upload_raw", "uploaded_hash", "uploaded_name",
                "validated_mapping"):
        st.session_state.pop(key, None)
    st.session_state.source_mode = mode
    st.session_state.upload_widget_number = st.session_state.get("upload_widget_number", 0) + 1


@st.cache_data(show_spinner=False)
def load_sample(signature):
    raw = pd.read_csv(SAMPLE_PATH, dtype=str)
    cols = [col for col in raw.columns if col != "timestamp"]
    return normalize_data(raw, time_col="timestamp", building_col=None,
                          load_cols=cols, unit="未确认")


def friendly_name(raw, example):
    if example and raw.startswith("Panther_office_"):
        return "办公建筑 " + raw.removeprefix("Panther_office_")
    return raw


def plot_lines(title, values, unit):
    fig = go.Figure()
    palette = ["#377DCC", "#138F83", "#ED9A41"]
    for idx, (label, series) in enumerate(values.items()):
        fig.add_trace(go.Scatter(x=series.index, y=series.to_numpy(),
                                 name=label, mode="lines",
                                 line=dict(color=palette[idx % 3], width=2.5)))
    fig.update_layout(title=title, height=400, hovermode="x unified", xaxis_title="时间",
                      yaxis_title=f"负荷（{unit}）" if unit != "未确认" else "负荷计量值",
                      paper_bgcolor="#FFF", plot_bgcolor="#FFF",
                      margin=dict(l=20, r=20, t=50, b=20))
    return fig


if "source_mode" not in st.session_state:
    st.session_state.source_mode = None

if st.session_state.source_mode is None:
    st.subheader("开始分析建筑用能数据")
    a, b = st.columns(2)
    with a:
        with st.container(border=True):
            st.markdown("### 上传我的数据")
            st.write("导入 CSV 或 XLSX 小时负荷数据，查看趋势、历史回测和治理报告。")
            if st.button("上传我的数据", type="primary", use_container_width=True):
                switch_source("upload")
                st.rerun()
    with b:
        with st.container(border=True):
            st.markdown("### 体验示例数据")
            st.write("使用内置建筑样例体验完整分析流程。")
            if st.button("体验示例数据", use_container_width=True):
                switch_source("example")
                st.rerun()
    st.stop()

mode = st.session_state.source_mode
with st.sidebar:
    st.markdown("### 数据来源")
    if st.button("更换数据来源", use_container_width=True):
        switch_source(None)
        st.rerun()

if mode == "example":
    try:
        stat = SAMPLE_PATH.stat()
        data, quality = load_sample((stat.st_size, stat.st_mtime_ns))
    except (OSError, DataInputError, ValueError) as exc:
        st.error(f"示例数据读取失败：{exc}")
        st.stop()
    st.session_state.active_df = data
    st.session_state.quality = quality
    st.session_state.source_name = "BDG2 示例数据"
    st.session_state.source_token = f"example:{stat.st_size}:{stat.st_mtime_ns}"
else:
    st.subheader("数据导入")
    st.write("上传整点小时数据。文件仅保留在当前会话，不写入服务器共享文件。")
    st.download_button("下载 CSV 数据模板", template_csv(), "建筑用能数据模板.csv",
                       mime="text/csv")
    uploaded = st.file_uploader("选择 CSV 或 XLSX 文件", type=["csv", "xlsx"],
                                key=f"upload_{st.session_state.get('upload_widget_number', 0)}")
    if uploaded is None:
        st.stop()
    content = uploaded.getvalue()
    fingerprint = sha256(content).hexdigest()
    if st.session_state.get("uploaded_hash") != fingerprint:
        for key in ("active_df", "quality", "analysis_cache", "analysis_cache_key",
                    "validated_mapping"):
            st.session_state.pop(key, None)
        try:
            st.session_state.upload_raw = read_upload(uploaded.name, content)
        except DataInputError as exc:
            st.error(str(exc))
            st.stop()
        st.session_state.uploaded_hash = fingerprint
        st.session_state.uploaded_name = uploaded.name
    raw = st.session_state.upload_raw
    st.markdown("#### 数据预览")
    st.dataframe(raw.head(10), hide_index=True, width="stretch")
    columns = list(raw.columns)
    time_index = next((i for i, c in enumerate(columns) if c.lower() in
                       ("timestamp", "time", "datetime", "日期时间")), 0)
    time_col = st.selectbox("时间列", columns, index=time_index)
    shape = st.radio("数据结构", ["宽表：每栋建筑一列", "长表：建筑编号和负荷各一列"],
                     horizontal=True)
    building_col = None
    if shape.startswith("长表"):
        candidates = [c for c in columns if c != time_col]
        id_index = next((i for i, c in enumerate(candidates) if c.lower() in
                         ("building_id", "building", "建筑编号")), 0)
        building_col = st.selectbox("建筑编号列", candidates, index=id_index)
        load_candidates = [c for c in candidates if c != building_col]
        if not load_candidates:
            st.error("缺少可选择的负荷列。")
            st.stop()
        load_index = next((i for i, c in enumerate(load_candidates) if c.lower() in
                           ("load", "power", "负荷")), 0)
        load_cols = [st.selectbox("负荷列", load_candidates, index=load_index)]
    else:
        candidates = [c for c in columns if c != time_col]
        load_cols = st.multiselect("建筑负荷列", candidates, default=candidates)
    unit = st.selectbox("负荷单位", ["未确认", "kW", "kWh"])
    mapping_token = (fingerprint, time_col, building_col, tuple(load_cols), unit)
    if st.button("验证数据并开始分析", type="primary"):
        try:
            data, quality = normalize_data(raw, time_col=time_col, building_col=building_col,
                                           load_cols=load_cols, unit=unit)
        except DataInputError as exc:
            st.error(str(exc))
        else:
            st.session_state.active_df = data
            st.session_state.quality = quality
            st.session_state.source_name = uploaded.name
            st.session_state.source_token = (
                f"upload:{fingerprint}:{time_col}:{building_col}:{','.join(load_cols)}:{unit}"
            )
            st.session_state.validated_mapping = mapping_token
            st.session_state.pop("analysis_cache_key", None)
            st.rerun()
    if "active_df" not in st.session_state:
        st.stop()
    if st.session_state.get("validated_mapping") != mapping_token:
        st.info("列映射或单位已调整，请重新验证数据后继续分析。")
        st.stop()

data = st.session_state.active_df
quality = st.session_state.quality
source_name = st.session_state.source_name
source_token = st.session_state.source_token
example = mode == "example"
with st.sidebar:
    selected_building = st.selectbox("当前建筑", list(data.columns),
                                     format_func=lambda name: friendly_name(name, example))
    st.caption("历史数据分析 · 非实时监测")

series = data[selected_building]
cache_key = (source_token, selected_building)
if st.session_state.get("analysis_cache_key") != cache_key:
    try:
        with st.spinner("正在分析当前建筑…"):
            analysis, unavailable_reason = analyze_building(series)
    except Exception as exc:
        analysis, unavailable_reason = None, f"预测分析未完成：{exc}"
    st.session_state.analysis_cache_key = cache_key
    st.session_state.analysis_cache = (analysis, unavailable_reason)
analysis, unavailable_reason = st.session_state.analysis_cache

if analysis:
    with st.sidebar:
        selected_day = st.selectbox("历史回测日期", analysis["days"],
                                    index=len(analysis["days"]) - 1, format_func=str)
    day_data = analysis["test"].loc[analysis["test"].index.date == selected_day].copy()
    thresholds = calculate_anomaly_threshold(series, day_data.index, analysis["test_start"])
    forecast = day_data["selected_prediction"].to_numpy()
    actual = day_data["actual"].to_numpy()
    valid_threshold = np.isfinite(thresholds)
    pred_mask = valid_threshold & (forecast > thresholds)
    actual_mask = valid_threshold & (actual > thresholds)
    pred_count = int(pred_mask.sum())
    actual_count = int(actual_mask.sum())
    risk_times = [t.strftime("%H:%M") for t in day_data.index[pred_mask]]
    max_exceedance = float(np.max(forecast[pred_mask] - thresholds[pred_mask])) if pred_count else 0.0

tabs = st.tabs(["数据导入", "用能总览", "负荷预测", "风险预警", "错峰决策", "治理报告"])
with tabs[0]:
    st.subheader("数据导入")
    a, b, c, d = st.columns(4)
    a.metric("当前建筑", friendly_name(selected_building, example))
    b.metric("数据记录", f"{int(series.notna().sum()):,} 小时")
    c.metric("数据质量", quality["status"])
    d.metric("负荷单位", quality["unit"])
    st.write(f"数据覆盖：{quality['start']} 至 {quality['end']}")
    if quality["missing_hours"] or quality["missing_values"]:
        st.write(f"缺少时间记录 {quality['missing_hours']} 小时；空缺或无效负荷值 {quality['missing_values']} 处。缺失数据未填为 0。")
    with st.expander("数据来源与质量详情", expanded=False):
        st.write(f"来源：{source_name}")
        st.write(f"原始建筑编号：{selected_building}")
        st.write(f"原文件行数：{quality['records']}；无法转换的负荷值：{quality['invalid_values']}。")
        st.write("kW 为功率，kWh 为小时电量；单位由上传者选择，系统不做自动换算。")
    quality_pdf = build_quality_pdf({"building": selected_building, "source": source_name,
                                     "quality": quality})
    st.download_button("下载数据质量说明（PDF）", quality_pdf,
                       file_name="建筑数据质量说明.pdf", mime="application/pdf")

with tabs[1]:
    st.subheader("用能总览")
    st.caption(f"{friendly_name(selected_building, example)} · {quality['start'][:10]} 至 {quality['end'][:10]}")
    a, b, c = st.columns(3)
    a.metric("平均小时负荷", f"{series.mean():.2f}")
    b.metric("最高小时负荷", f"{series.max():.2f}")
    c.metric("有效记录", f"{int(series.notna().sum()):,} 小时")
    st.plotly_chart(plot_lines("最近 30 天负荷趋势", {"历史负荷": series.tail(24 * 30)},
                               quality["unit"]), width="stretch")
    if analysis is None:
        st.info(unavailable_reason)
    else:
        st.write(f"所选回测日：{selected_day} · 预测高负荷时段：{pred_count} 小时")

with tabs[2]:
    st.subheader("负荷预测")
    if analysis is None:
        st.info(unavailable_reason)
    else:
        m = analysis["metrics"]
        a, b, c = st.columns(3)
        a.metric("模型测试集 MAE", f"{m['mae']:.2f}")
        b.metric("历史基线测试集 MAE", f"{m['baseline_mae']:.2f}")
        c.metric("验证集推荐方法", analysis["method"])
        st.plotly_chart(plot_lines("所选日期 24 小时负荷回测", {
            "实际负荷": day_data["actual"], "推荐方法预测": day_data["selected_prediction"],
            "历史同期基线": day_data["baseline"]}, quality["unit"]), width="stretch")
        st.caption("历史回测结果；当前版本不提供未来 24 小时预测。")
        with st.expander("数据与方法说明", expanded=False):
            st.write(f"训练截止：{analysis['train_end'].date()}；测试起始：{analysis['test_start'].date()}。按时间先后划分，验证集选择方法，测试集仅用于评估。")
            st.write(f"验证集模型 MAE：{m['validation_mae']:.2f}；验证集历史基线 MAE：{m['validation_baseline_mae']:.2f}；测试集模型 RMSE：{m['rmse']:.2f}。")
            st.write("预测特征仅使用目标时刻 24 小时前及更早的实际负荷；历史基线使用 168 小时前负荷。")

with tabs[3]:
    st.subheader("风险预警")
    if analysis is None:
        st.info(unavailable_reason)
    elif not valid_threshold.all():
        st.info("相同时段的历史参考样本不足，当前日期无法可靠计算完整风险阈值。")
    else:
        if pred_count:
            a, b = st.columns(2)
            a.metric("预测高负荷时段", f"{pred_count} 小时")
            b.metric("最大超阈幅度", f"{max_exceedance:.2f}")
            st.write("重点关注：" + "、".join(risk_times))
        else:
            st.write("**未识别高负荷风险**。预测负荷未超过历史统计阈值。")
        st.plotly_chart(plot_lines("预测负荷与历史统计阈值", {
            "推荐方法预测": day_data["selected_prediction"],
            "历史统计阈值": pd.Series(thresholds, index=day_data.index)},
            quality["unit"]), width="stretch")
        st.metric("历史实际超阈", f"{actual_count} 小时")
        if actual_count:
            st.write("实际超阈时间：" + "、".join(t.strftime("%H:%M") for t in day_data.index[actual_mask]))
        with st.expander("数据与方法说明", expanded=False):
            st.write("阈值由测试集之前的同星期、同小时历史负荷 95% 分位数计算，每组至少需要 3 个样本。")
            st.write("结果为统计高负荷异常提示，不能直接诊断设备故障。")

with tabs[4]:
    st.subheader("错峰决策")
    if analysis is None:
        st.info(unavailable_reason)
    else:
        st.markdown("#### 理论错峰情景分析")
        target_pct = st.slider("目标削峰比例", 5, 20, 10, 1, format="%d%%")
        adjusted, shifted, reduction = peak_shaving_scenario(forecast, target_pct)
        a, b, c = st.columns(3)
        a.metric("原预测峰值", f"{float(np.max(forecast)):.2f}")
        b.metric("理论削峰比例", f"{reduction:.1f}%")
        c.metric("负荷转移需求量", f"{shifted:.2f}")
        st.plotly_chart(plot_lines("理论错峰前后负荷", {
            "原预测负荷": pd.Series(forecast, index=day_data.index),
            "理论调整后负荷": pd.Series(adjusted, index=day_data.index)},
            quality["unit"]), width="stretch")
        with st.expander("情景假设与实施条件", expanded=False):
            st.write("假设负荷可在当天转移且总量不变；需核查设备容量、舒适度和运行约束。情景不代表已执行调度。")
            st.write("转移需求量按小时累计；单位未知时不换算为电量、电费或碳减排。")

with tabs[5]:
    st.subheader("治理报告")
    st.write(f"{friendly_name(selected_building, example)} · 历史数据分析")
    if analysis is None:
        st.info("当前数据不足以生成预测与风险结论。可在“数据导入”下载数据质量说明。")
    else:
        risk_available = bool(valid_threshold.all())
        if not risk_available:
            st.info("风险阈值样本不足，报告中的风险结论将标注为不可评估。")
        report_target = target_pct
        adjusted, shifted, reduction = peak_shaving_scenario(forecast, report_target)
        m = analysis["metrics"]
        if risk_available and pred_count:
            recommendation = "核查预测超阈时段的设备计划，评估非关键负荷的错峰条件。"
        elif risk_available:
            recommendation = "保持常规巡检，关注后续负荷趋势。"
        else:
            recommendation = "补充同星期、同小时的历史样本后再评估高负荷风险。"
        report = {
            "building": selected_building,
            "display_building": friendly_name(selected_building, example),
            "source": source_name,
            "coverage": f"{quality['start']} 至 {quality['end']}",
            "unit": quality["unit"],
            "date": str(selected_day),
            "mean_load": float(series.mean()),
            "peak_load": float(series.max()),
            "mae": float(m["mae"]), "rmse": float(m["rmse"]),
            "baseline_mae": float(m["baseline_mae"]),
            "recommended_method": analysis["method"],
            "pred_risk_count": pred_count if risk_available else None,
            "actual_risk_count": actual_count if risk_available else None,
            "risk_text": "、".join(risk_times) if risk_available and pred_count else "无",
            "max_exceedance": max_exceedance if risk_available else None,
            "target_pct": report_target,
            "original_peak": float(np.max(forecast)),
            "adjusted_peak": float(np.max(adjusted)),
            "reduction": reduction, "shifted": shifted,
            "recommendation": recommendation,
            "train_end": str(analysis["train_end"].date()),
            "test_start": str(analysis["test_start"].date()),
        }
        st.markdown("#### 01 运行结论")
        st.write(f"平均小时负荷 {report['mean_load']:.2f}；最高小时负荷 {report['peak_load']:.2f}（{quality['unit']}）。")
        st.markdown("#### 02 预测评估")
        st.write(f"验证集推荐：{analysis['method']}。测试集模型 MAE {m['mae']:.2f}；历史基线 MAE {m['baseline_mae']:.2f}。")
        st.markdown("#### 03 风险分析")
        st.write(f"预测超阈 {pred_count} 小时；历史实际超阈 {actual_count} 小时。" if risk_available else "历史样本不足，风险不可评估。")
        st.markdown("#### 04 理论错峰方案")
        st.write(f"目标 {report_target}%；理论削峰 {reduction:.1f}%；负荷转移需求量 {shifted:.2f}。")
        st.markdown("#### 05 治理建议")
        st.write(recommendation)
        st.caption("实施前须核查设备和管理约束；理论结果不代表已实现节能。")
        try:
            pdf_bytes = build_pdf_report(report, day_data)
        except Exception as exc:
            st.error(f"PDF 报告生成失败：{exc}")
        else:
            st.download_button("下载 PDF 治理报告", pdf_bytes,
                               file_name=f"building_energy_report_{selected_day}.pdf",
                               mime="application/pdf", width="stretch")
