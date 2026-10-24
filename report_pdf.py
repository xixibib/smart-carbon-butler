"""会话分析的 A4 中文 PDF 报告。"""

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib.dates as mdates
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


NAVY = colors.HexColor("#17385F")
BLUE = colors.HexColor("#377DCC")
MUTED = colors.HexColor("#64748B")
PALE = colors.HexColor("#F1F5FA")


def _chinese_font():
    """在可用时嵌入中文 TTF；Linux 无字体时退回 ReportLab CID 字体。"""
    candidates = (
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/msyh.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    )
    if "ZCChinese" in pdfmetrics.getRegisteredFontNames():
        return "ZCChinese", next((path for path in candidates if path.is_file()), None)
    for path in candidates:
        if path.is_file() and path.suffix.lower() == ".ttf":
            try:
                pdfmetrics.registerFont(TTFont("ZCChinese", str(path)))
                return "ZCChinese", path
            except Exception:
                pass
    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light", next((path for path in candidates if path.is_file()), None)


def _styles(font_name):
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ZCTitle", fontName=font_name, fontSize=18,
                              leading=26, textColor=NAVY, spaceAfter=8))
    styles.add(ParagraphStyle(name="ZCHead", fontName=font_name, fontSize=12,
                              leading=19, textColor=NAVY, spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name="ZCBody", fontName=font_name, fontSize=9.3,
                              leading=15, textColor=NAVY, spaceAfter=5))
    styles.add(ParagraphStyle(name="ZCSmall", fontName=font_name, fontSize=8,
                              leading=12, textColor=MUTED, spaceAfter=4))
    return styles


def _paragraph(value, styles, name="ZCBody"):
    return Paragraph(escape(str(value)), styles[name])


def _table(rows, styles):
    width = A4[0] - 38 * mm
    cells = [[_paragraph(a, styles), _paragraph(b, styles)] for a, b in rows]
    table = Table(cells, colWidths=[49 * mm, width - 49 * mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [PALE, colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _chart(day_data, font_path):
    fig = Figure(figsize=(8, 3.1), dpi=150, facecolor="white")
    ax = fig.add_subplot(111)
    ax.plot(day_data.index, day_data["actual"], color="#138F83", label="实际负荷", linewidth=2)
    ax.plot(day_data.index, day_data["selected_prediction"], color="#377DCC",
            label="推荐方法预测", linewidth=2)
    ax.plot(day_data.index, day_data["baseline"], color="#ED9A41",
            label="历史同期基线", linewidth=1.6, linestyle="--")
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.grid(axis="y", color="#E3E9EF")
    ax.spines[["top", "right"]].set_visible(False)
    if font_path:
        ax.legend(prop=FontProperties(fname=str(font_path)), frameon=False, ncol=3)
    else:
        ax.legend(["Actual", "Selected forecast", "Historical baseline"], frameon=False, ncol=3)
    fig.tight_layout()
    output = BytesIO()
    FigureCanvasAgg(fig).print_png(output)
    output.seek(0)
    return output


def _finish(story, font_name, title):
    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, leftMargin=19 * mm,
                            rightMargin=19 * mm, topMargin=18 * mm,
                            bottomMargin=20 * mm, title=title, author="智碳管家")
    def footer(canvas, pdf_doc):
        canvas.saveState()
        canvas.setStrokeColor(PALE)
        canvas.line(19 * mm, 15 * mm, A4[0] - 19 * mm, 15 * mm)
        canvas.setFont(font_name, 8)
        canvas.setFillColor(MUTED)
        canvas.drawString(19 * mm, 10 * mm, "智碳管家 · 建筑用能分析")
        canvas.drawRightString(A4[0] - 19 * mm, 10 * mm, f"第 {pdf_doc.page} 页")
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    result = stream.getvalue()
    if not result.startswith(b"%PDF-") or b"%%EOF" not in result[-1024:]:
        raise ValueError("PDF 文件格式校验失败")
    return result


def build_quality_pdf(info):
    font_name, _ = _chinese_font()
    styles = _styles(font_name)
    quality = info["quality"]
    p = lambda value, style="ZCBody": _paragraph(value, styles, style)
    story = [
        p("建筑数据质量说明", "ZCTitle"),
        _table([
            ("建筑编号", info["building"]),
            ("数据来源", info["source"]),
            ("覆盖时间", f"{quality['start']} 至 {quality['end']}"),
            ("原始记录", str(quality["records"])),
            ("数据状态", quality["status"]),
            ("缺少时间记录", f"{quality['missing_hours']} 小时"),
            ("空缺或无效负荷", str(quality["missing_values"])),
            ("负荷单位", quality["unit"]),
        ], styles),
        Spacer(1, 5 * mm),
        p("缺失数据没有替换为零。预测需要至少 45 天整点数据、足够的有效特征记录，以及完整的测试日期。"),
        p("kW 与 kWh 含义不同；系统不自动换算单位。", "ZCSmall"),
    ]
    return _finish(story, font_name, "建筑数据质量说明")


def build_pdf_report(report, day_data):
    font_name, font_path = _chinese_font()
    styles = _styles(font_name)
    p = lambda value, style="ZCBody": _paragraph(value, styles, style)
    risk = (f"{report['pred_risk_count']} 小时；{report['risk_text']}"
            if report["pred_risk_count"] is not None else "历史参考样本不足，未评估")
    actual = (f"{report['actual_risk_count']} 小时"
              if report["actual_risk_count"] is not None else "未评估")
    story = [
        p("智碳管家 · 建筑用能治理报告", "ZCTitle"),
        p(f"{report['display_building']} · {report['date']} 历史分析", "ZCSmall"),
        p("01 运行结论", "ZCHead"),
        _table([
            ("平均小时负荷", f"{report['mean_load']:.2f} {report['unit']}"),
            ("最高小时负荷", f"{report['peak_load']:.2f} {report['unit']}"),
            ("预测高负荷风险", risk),
        ], styles),
        p("02 预测评估", "ZCHead"),
        _table([
            ("验证集推荐方法", report["recommended_method"]),
            ("模型测试集 MAE", f"{report['mae']:.2f}"),
            ("模型测试集 RMSE", f"{report['rmse']:.2f}"),
            ("历史基线测试集 MAE", f"{report['baseline_mae']:.2f}"),
        ], styles),
        Spacer(1, 4 * mm),
    ]
    chart = _chart(day_data, font_path)
    width = A4[0] - 38 * mm
    story += [
        Image(chart, width=width, height=width * 3.1 / 8),
        p("图：所选历史日期的实际负荷、推荐方法预测与历史基线。", "ZCSmall"),
        p("03 风险分析", "ZCHead"),
        _table([
            ("预测超阈时段", risk),
            ("历史实际超阈", actual),
            ("最大超阈幅度", f"{report['max_exceedance']:.2f}" if report["max_exceedance"] is not None else "未评估"),
        ], styles),
        p("04 理论错峰方案", "ZCHead"),
        _table([
            ("目标削峰比例", f"{report['target_pct']}%"),
            ("原预测峰值", f"{report['original_peak']:.2f}"),
            ("理论调整后峰值", f"{report['adjusted_peak']:.2f}"),
            ("理论削峰比例", f"{report['reduction']:.1f}%"),
            ("负荷转移需求量", f"{report['shifted']:.2f}（按小时累计）"),
        ], styles),
        p("05 治理建议", "ZCHead"),
        p(report["recommendation"]),
        p("实施前应核查设备容量、运行时间、舒适度及实际管理要求。"),
        p("数据溯源与方法边界", "ZCHead"),
        p(f"原始建筑编号：{report['building']}；数据来源：{report['source']}；覆盖时间：{report['coverage']}。", "ZCSmall"),
        p(f"负荷单位：{report['unit']}。训练截止：{report['train_end']}；测试起始：{report['test_start']}。"
          "按时间顺序划分，方法由验证集选定；测试集仅用于最终评估。", "ZCSmall"),
        p("风险依据同星期、同小时历史负荷的 95% 分位数，只表示统计高负荷异常，不能诊断设备故障。"
          "错峰假设负荷可在当天自由转移且总量不变，不代表实际执行或节电、节费、减排。", "ZCSmall"),
    ]
    return _finish(story, font_name, "智碳管家建筑用能治理报告")
