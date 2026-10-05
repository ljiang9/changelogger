# changelogger

git log 进，release notes 出。

把一个版本区间的提交记录交给 LLM，生成结构化的中文发布说明（Highlights / 新功能 / 修复 / 破坏性变更 / 贡献者）。也支持**纯本地**的 `--group` 模式：按 conventional-commit 类型分组，不花一分钱 API。

纯标准库，零依赖。

## 安装

```bash
# 直接跑，无需安装
python3 -m changelogger --help
# 或
python3 changelogger.py --help
```

需要 LLM 模式时设置 key（二选一）：

```bash
export OPENAI_API_KEY="sk-..."
# 也支持 OpenAI 兼容网关
export OPENAI_BASE_URL="https://your-gateway/v1"
```

## 用法

```bash
# 默认：上一个 tag 到 HEAD 的提交 → LLM 写中文发布说明
changelogger

# 指定区间
changelogger v1.2.0..HEAD

# 先看会发什么给模型（不联网）
changelogger v1.2.0..HEAD --dry-run

# 纯本地分组（不需要 key）
changelogger --group
changelogger v1.2.0..HEAD --group --json

# 写文件 / 英文
changelogger -o RELEASE.md
changelogger --lang en --group
```

## 参数

| 参数 | 说明 |
|---|---|
| `rev` | 版本区间，如 `v1.2.0..HEAD`；默认上一个 tag..HEAD，无 tag 则最近 20 条 |
| `--group` | 纯本地：按提交类型分组，不调 LLM |
| `--dry-run` | 展示收集到的提交 + prompt，不发起网络请求 |
| `-o FILE` | 结果写入文件 |
| `--json` | 结构化 JSON 输出（分组模式） |
| `--lang zh/en` | 输出语言（默认 zh） |
| `--model` | 模型名（默认 `gpt-4o-mini`，可用 `OPENAI_MODEL` 覆盖） |

破坏性变更识别：提交信息里的 `!`（如 `feat!: ...`）或 `BREAKING CHANGE`。

## 诚实说明

- **发布说明的质量取决于提交信息的质量**：提交写得潦草，生成的 notes 也只能是"垃圾进、垃圾出"。建议配合 conventional commits 使用。
- 最多收录 100 条提交，超出会明确标注截断。
- LLM 可能误判破坏性变更：发版前请人工过一眼。
- `--group` 是确定性的本地分组，适合 CI；LLM 模式适合对外发布稿。

## License

MIT © 2026 ljiang9
