# 智碳管家

面向建筑能源管理的中文 Streamlit 应用。可上传 CSV/XLSX 小时负荷数据，或使用五栋办公建筑的 BDG2 小型示例。上传数据仅保存在当前 Streamlit 会话中，不会写入共享文件。

## 本地运行

建议 Python 3.10 或更新版本：

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Windows 已有环境可使用 `E:\Anaconda3\envs\math\python.exe -m streamlit run app.py`。启动后打开终端显示的本地地址，通常为 `http://localhost:8501`。

## 上传数据

支持 CSV（UTF-8 或 GB18030）和 XLSX，单文件上限 15 MB、10 万行、30 列。点击首页“上传我的数据”，预览文件并选择时间列、数据结构、建筑列/负荷列和单位，然后验证。

- 宽表：`timestamp,Building_A,Building_B`，每栋建筑一列。
- 长表：`timestamp,building_id,load`，每行是一栋建筑的一个小时。
- 时间必须为整点小时；系统不会自行把分钟数据或 kW/kWh 互相换算。
- 重复时间、负值、无法解析的时间会被拒绝；缺失负荷保留为空值，并显示数据质量。

当历史跨度不足 45 天、有效小时不足 900，或无法形成完整的训练、验证和测试日期时，仍可查看趋势和下载质量说明；预测、风险、错峰和治理报告中的相应结论不生成。当前版本只做历史回测，不提供未来 24 小时预测。

## 方法与边界

模型使用目标时刻 24 小时前及更早的负荷特征；历史基线使用 168 小时前的负荷。按时间顺序划分数据，用验证集选择模型或基线，测试集仅用于评估。风险阈值为测试集之前同星期、同小时历史负荷的 95% 分位数，至少需要 3 个样本。理论错峰假设部分负荷可以在当天转移且总量不变，不等于实际设备调度或节能。

示例文件是 `data/electricity_sample_5buildings.csv`。完整原始数据 `data/electricity_cleaned.csv` 不参与运行，也被 .gitignore 排除。分享示例前请自行确认数据集许可。

## 部署到 Streamlit Community Cloud

1. 在 GitHub 新建私有或公开仓库，把本项目中的 `app.py`、`analysis_engine.py`、`data_input.py`、`report_pdf.py`、`requirements.txt`、`packages.txt`、`.streamlit/config.toml`、`README.md`、`.gitignore` 与示例 CSV 提交进去。不要提交原始大数据、上传文件或密钥。
2. 在 Streamlit Community Cloud 连接该仓库，主文件路径填写 `app.py`，选择受支持的 Python 版本。
3. 部署后用公开 URL 检查示例、上传、切换建筑和 PDF。应用不含账号系统；知道 URL 的访问者都可能使用它。若需要权限控制，应在上线前接入独立认证。

本仓库不包含自动发布步骤，部署需由有权限的仓库和云平台账号持有人操作。
