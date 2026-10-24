"""会话内上传数据的读取、映射与质量检查。"""

from io import BytesIO
from zipfile import BadZipFile, ZipFile

import numpy as np
import pandas as pd


MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 30
MAX_HOURS = 100_000


class DataInputError(ValueError):
    pass


def read_upload(filename: str, content: bytes) -> pd.DataFrame:
    if not content:
        raise DataInputError("文件为空，请重新选择 CSV 或 XLSX 文件。")
    if len(content) > MAX_FILE_BYTES:
        raise DataInputError("文件超过 15 MB。请拆分数据后重新上传。")
    suffix = filename.lower().rsplit(".", 1)[-1]
    if suffix == "csv":
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                frame = pd.read_csv(
                    BytesIO(content), encoding=encoding, sep=None,
                    engine="python", dtype=str, nrows=MAX_ROWS + 1
                )
                break
            except UnicodeDecodeError:
                continue
            except Exception as exc:
                raise DataInputError(f"CSV 读取失败：{exc}") from exc
        else:
            raise DataInputError("CSV 编码无法识别，请保存为 UTF-8 格式。")
    elif suffix == "xlsx":
        try:
            with ZipFile(BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
                    raise DataInputError("XLSX 解压后数据过大，请拆分文件。")
            frame = pd.read_excel(
                BytesIO(content), dtype=str, nrows=MAX_ROWS + 1,
                engine="openpyxl"
            )
        except DataInputError:
            raise
        except (BadZipFile, Exception) as exc:
            raise DataInputError(f"XLSX 读取失败：{exc}") from exc
    else:
        raise DataInputError("仅支持 CSV 和 XLSX 文件。")
    if frame.empty:
        raise DataInputError("文件没有可用的数据行。")
    if len(frame) > MAX_ROWS or len(frame.columns) > MAX_COLUMNS:
        raise DataInputError("数据超过 10 万行或 30 列，请缩小文件后上传。")
    frame.columns = [str(col).strip() for col in frame.columns]
    if len(set(frame.columns)) != len(frame.columns):
        raise DataInputError("文件存在重复列名，请先调整列名。")
    return frame


def normalize_data(
    raw: pd.DataFrame, *, time_col: str, building_col: str | None,
    load_cols: list[str], unit: str
) -> tuple[pd.DataFrame, dict]:
    if time_col not in raw.columns:
        raise DataInputError("请选择有效的时间列。")
    if not load_cols or any(col not in raw.columns for col in load_cols):
        raise DataInputError("请选择至少一个有效的负荷列。")
    if building_col and (building_col not in raw.columns or len(load_cols) != 1):
        raise DataInputError("长表需要一个建筑编号列和一个负荷列。")
    if time_col in load_cols or building_col in load_cols or time_col == building_col:
        raise DataInputError("时间、建筑编号和负荷列不能重复选择。")

    times = pd.to_datetime(raw[time_col], errors="coerce")
    if times.isna().any():
        raise DataInputError(f"时间列有 {int(times.isna().sum())} 条记录无法解析，请修正后上传。")
    if getattr(times.dt, "tz", None) is not None:
        raise DataInputError("当前不支持带时区的时间，请先统一为本地时间并移除时区标记。")
    if (times.dt.minute.ne(0) | times.dt.second.ne(0) | times.dt.microsecond.ne(0)).any():
        raise DataInputError("当前仅支持整点小时数据；分钟级数据请先按业务单位汇总为小时数据。")

    if building_col:
        ids = raw[building_col].astype("string").str.strip()
        if ids.isna().any() or ids.eq("").any():
            raise DataInputError("建筑编号列存在空值。")
        if ids.nunique() > 20:
            raise DataInputError("单次最多分析 20 栋建筑，请拆分文件。")
        pairs = pd.DataFrame({"timestamp": times, "building": ids})
        if pairs.duplicated().any():
            raise DataInputError("同一建筑存在重复时间记录，请先合并或删除重复记录。")
        values = pd.to_numeric(raw[load_cols[0]], errors="coerce")
        invalid = int((values.isna() & raw[load_cols[0]].notna()).sum())
        pairs["load"] = values
        frame = pairs.pivot(index="timestamp", columns="building", values="load")
        frame.columns.name = None
    else:
        if times.duplicated().any():
            raise DataInputError("存在重复时间记录，请先合并或删除重复记录。")
        invalid = 0
        converted = {}
        for col in load_cols:
            values = pd.to_numeric(raw[col], errors="coerce")
            invalid += int((values.isna() & raw[col].notna()).sum())
            converted[col] = values.to_numpy()
        frame = pd.DataFrame(converted, index=pd.DatetimeIndex(times, name="timestamp"))

    frame = frame.sort_index().astype(float)
    if np.isinf(frame.to_numpy()).any():
        raise DataInputError("负荷列存在无穷大数值，请先修正。")
    if (frame < 0).any().any():
        raise DataInputError("负荷列存在负值，请核对计量数据后重新上传。")
    if frame.notna().sum().eq(0).any():
        raise DataInputError("至少一栋建筑没有有效负荷数值，请检查列映射。")

    hours = int((frame.index.max() - frame.index.min()) / pd.Timedelta(hours=1)) + 1
    if hours > MAX_HOURS:
        raise DataInputError("数据时间跨度过大，请缩小范围后上传。")
    full_index = pd.date_range(frame.index.min(), frame.index.max(), freq="h", name="timestamp")
    gaps = len(full_index) - len(frame)
    frame = frame.reindex(full_index)
    missing = int(frame.isna().sum().sum())
    status = "可分析" if gaps == 0 and missing == 0 else "可分析，部分时段缺失"
    quality = {
        "status": status,
        "unit": unit,
        "start": str(frame.index.min()),
        "end": str(frame.index.max()),
        "records": len(raw),
        "hourly_rows": len(frame),
        "missing_values": missing,
        "missing_hours": gaps,
        "invalid_values": invalid,
        "buildings": list(frame.columns),
    }
    return frame, quality


def template_csv() -> bytes:
    return ("timestamp,Building_A,Building_B\n"
            "2026-01-01 00:00,120,85\n"
            "2026-01-01 01:00,110,80\n").encode("utf-8-sig")
