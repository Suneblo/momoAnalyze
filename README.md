# MomoAnalyze

MomoAnalyze 是一个面向墨墨背单词学习记录的本地分析工具。它从墨墨 API 同步数据，将每日快照写入 SQLite，并在浏览器中提供复习趋势、当日时间点、记忆持久度、FSRS 预测、单词追踪和复制摘要等视图。

项目以本地运行为主：数据库、API Token 和导出文件都保存在本机，不提交到 Git。

## 主要功能

- 按日期浏览学习进度、复习任务、状态分布和记忆持久度变化。
- 查看当日多个同步时间点，并自由对比任意两个快照。
- 使用 FSRS 生成预测柱状图、概率模型、表格和按需明细。
- 使用固定词数分桶查看记忆持久度与学习次数的联合分布。
- 搜索库存单词，追踪预测路径和历史状态变化。
- 从文章中提取未学习词，并可排除英文变形词。
- 管理云词本，并将筛选结果添加到学习计划。
- 发现墨墨 API 中缺少拼写的自定义词时，使用上下文辅助补全。

## 技术结构

- 前端：Vue 3、Pinia、Element Plus、ECharts、Plotly。
- 后端：Python 标准库 HTTP 服务。
- 存储：SQLite `compact_v4_word_key` schema。
- 配置：项目根目录唯一配置文件 `app.json`。
- 启动入口：`python start.py`。

## 首次启动

环境要求：

- Python 3.11 或更高版本
- Node.js 和 npm

在项目根目录运行：

```bash
python start.py
```

首次启动会自动完成以下工作：

1. 缺少 `app.json` 时生成默认配置。
2. 缺少数据库时按 `app.json` 的 `paths.database` 创建 SQLite 文件和 schema。
3. 缺少 Python 依赖时执行 `pip install -r requirements.txt`。
4. 缺少前端依赖时执行 `npm install`。
5. 启动 Python 后端和 Vite 前端，并打开浏览器。

不希望启动器自动下载依赖时，使用：

```bash
python start.py --no-install
```

不希望自动打开浏览器时，使用：

```bash
python start.py --no-open
```

Android / Termux 下可使用：

```bash
python start.py --tmux
```

## API Token

联网同步前，将墨墨 API Token 写入项目根目录的 `token.txt`。该文件已加入 `.gitignore`，不要提交或分享。

启动后，在页面顶部点击“联网获取最新数据”即可同步当前数据。

## 配置

所有运行配置统一放在根目录 [app.json](./app.json)。项目不再读取旧的 `config/app.json` 或 `ports.json`。

常用字段：

```json
{
  "server": {
    "backend": { "host": "127.0.0.1", "port": 8000 },
    "frontend": { "host": "0.0.0.0", "port": 5173 }
  },
  "paths": {
    "database": "data/momo.sqlite",
    "token": "token.txt"
  }
}
```

`memoryAlgorithm` 用于调整 FSRS 预测参数、墨墨作答到 FSRS Rating 的映射和预测规模。

## 旧数据库迁移

运行时代码只支持 `compact_v4_word_key`。旧数据库需要显式迁移，不提供运行时兼容层：

```bash
python scripts/migrate_v4_word_key.py --db data/momo.sqlite
```

迁移前建议备份原数据库。

## 常用命令

```bash
# 启动开发服务
python start.py

# 仅构建前端
npm run build

# 重新安装前端依赖并固定 Windows 稳定依赖版本
python start.py --fix-frontend

# 查看启动参数
python start.py --help
```

## 本地文件

以下目录或文件不会提交到 Git：

- `data/`：SQLite 数据库
- `token.txt`：墨墨 API Token
- `node_modules/`：前端依赖
- `dist/`：前端构建产物
- `logs/`：运行日志
- `exports/`：导出数据
- `debug_api_output/`：调试输出
