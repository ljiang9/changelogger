#!/usr/bin/env python3
"""changelogger - git log 进，release notes 出。

把一个版本区间的提交记录喂给 LLM，生成结构化的中文（或英文）发布说明。
也提供纯本地方案：--group 按 conventional-commit 类型分组，无需 API key。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error

VERSION = "0.1.0"
MAX_COMMITS = 100
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def eprint(msg):
    sys.stderr.write(msg + "\n")


def run_git(args, cwd):
    try:
        p = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=30
        )
    except FileNotFoundError:
        eprint("error: 找不到 git 命令，请先安装 git。")
        sys.exit(1)
    except subprocess.TimeoutExpired:
        eprint("error: git 命令超时。")
        sys.exit(1)
    return p


def ensure_git_repo(cwd):
    p = run_git(["rev-parse", "--is-inside-work-tree"], cwd)
    if p.returncode != 0:
        eprint("error: 当前目录不是 git 仓库，请在 git 仓库内运行 changelogger。")
        sys.exit(1)


def latest_tag(cwd):
    p = run_git(["describe", "--tags", "--abbrev=0"], cwd)
    if p.returncode == 0 and p.stdout.strip():
        return p.stdout.strip()
    return None


def resolve_range(rev, cwd):
    """返回 (git log 额外参数列表, label)。rev 可为 None 表示自动推断。"""
    if rev:
        return [rev], rev
    tag = latest_tag(cwd)
    if tag:
        return [tag + "..HEAD"], tag + "..HEAD"
    # 无 tag：取最近 20 条（-n 计数模式，提交不足 20 条也不会报错）
    return ["-n", "20", "HEAD"], "最近 20 个提交（无 tag，默认回退）"


COMMIT_SEP = "\x1e"
FIELD_SEP = "\x1f"


def collect_commits(rev_args, cwd):
    fmt = COMMIT_SEP.join(["%H", "%an", "%ae", "%ad", "%s", "%b"]) + FIELD_SEP
    p = run_git(
        ["log", "--format=" + fmt, "--date=short"] + rev_args + ["--"],
        cwd,
    )
    if p.returncode != 0:
        eprint("error: 无法读取提交记录（%s）：%s" % (" ".join(rev_args), p.stderr.strip()))
        sys.exit(1)
    # 注意：\x1e/\x1f 在 Python 里算空白字符（isspace() 为 True），
    # 所以这里绝不能用裸 .strip()，否则会吞掉分隔符。只剥换行。
    raw = p.stdout.strip("\r\n")
    if not raw.strip("\x1e\x1f \r\n"):
        return [], False
    commits = []
    truncated = False
    for chunk in raw.split(FIELD_SEP):
        # git 会在每条记录后补一个换行，它落在下一条记录开头，需剥掉
        chunk = chunk.lstrip("\r\n")
        if not chunk.strip("\x1e\x1f \r\n"):
            continue
        parts = chunk.split(COMMIT_SEP)
        # 空 body 时 git 会省略末尾分隔符，补齐空字段
        while len(parts) < 6:
            parts.append("")
        if len(parts) < 5:
            continue
        sha, author, email, date, subject, body = parts[:6]
        commits.append(
            {
                "sha": sha[:8],
                "author": author,
                "email": email,
                "date": date,
                "subject": subject.strip(),
                "body": body.strip(),
            }
        )
        if len(commits) >= MAX_COMMITS:
            truncated = True
            break
    return commits, truncated


BREAKING_RE = re.compile(r"BREAKING[ -]CHANGE", re.IGNORECASE)
CONV_RE = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(?P<scope>\([^)]*\))?(?P<bang>!)?\s*:\s*(?P<desc>.+)$",
    re.DOTALL,
)

SECTION_TITLES = {
    "zh": {
        "breaking": "破坏性变更",
        "feat": "新功能",
        "fix": "修复",
        "docs": "文档",
        "perf": "性能",
        "refactor": "重构",
        "test": "测试",
        "other": "其他",
        "contributors": "贡献者",
    },
    "en": {
        "breaking": "Breaking Changes",
        "feat": "Features",
        "fix": "Bug Fixes",
        "docs": "Documentation",
        "perf": "Performance",
        "refactor": "Refactors",
        "test": "Tests",
        "other": "Others",
        "contributors": "Contributors",
    },
}

GROUP_ORDER = ["breaking", "feat", "fix", "perf", "refactor", "docs", "test", "other"]


def is_breaking(commit):
    if BREAKING_RE.search(commit["body"]) or BREAKING_RE.search(commit["subject"]):
        return True
    m = CONV_RE.match(commit["subject"])
    return bool(m and m.group("bang"))


def group_commits(commits, lang):
    groups = {k: [] for k in GROUP_ORDER}
    for c in commits:
        if is_breaking(c):
            groups["breaking"].append(c)
            continue
        m = CONV_RE.match(c["subject"])
        t = m.group("type") if m else "other"
        key = t if t in groups else "other"
        groups[key].append(c)
    return groups


def short_desc(commit):
    m = CONV_RE.match(commit["subject"])
    if m:
        return m.group("desc").strip()
    return commit["subject"].strip()


def render_grouped(groups, lang):
    t = SECTION_TITLES[lang]
    lines = []
    for key in GROUP_ORDER:
        items = groups[key]
        if not items:
            continue
        lines.append("## " + t[key])
        for c in items:
            lines.append("- %s (%s, %s)" % (short_desc(c), c["author"], c["sha"]))
        lines.append("")
    names = []
    seen = set()
    for key in GROUP_ORDER:
        for c in groups[key]:
            if c["author"] not in seen:
                seen.add(c["author"])
                names.append(c["author"])
    if names:
        lines.append("## " + t["contributors"])
        lines.append(", ".join(names))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_prompt(commits, lang, truncated):
    lines = []
    for c in commits:
        entry = "- [%s] %s | %s | %s" % (c["sha"], c["date"], c["author"], c["subject"])
        if c["body"]:
            body = c["body"].replace("\n", " / ")
            if len(body) > 300:
                body = body[:300] + "…"
            entry += " // " + body
        lines.append(entry)
    commit_text = "\n".join(lines)
    note = ""
    if truncated:
        note = "（注意：提交数超过 %d，只收录了最新的 %d 条。）" % (MAX_COMMITS, MAX_COMMITS)
    if lang == "en":
        system = (
            "You are a release-notes writer. Given a list of git commits, write "
            "concise release notes in English with sections: Highlights, Features, "
            "Bug Fixes, Breaking Changes (only if any), Contributors. "
            "Derive Breaking Changes from '!' markers or BREAKING CHANGE notes. "
            "Group trivial chores under Others. Output Markdown only."
        )
        user = "Commits%s:\n\n%s" % (note, commit_text)
    else:
        system = (
            "你是一名发布说明撰写助手。根据下面给出的 git 提交列表，用中文写一份简洁的 "
            "release notes，包含章节：Highlights / 新功能 / 修复 / 破坏性变更（没有就不写）/ "
            "贡献者。破坏性变更从提交信息里的 `!` 标记或 BREAKING CHANGE 识别。琐碎的 "
            "chore 归入「其他」。只输出 Markdown，不要多余解释。"
        )
        user = "提交列表%s：\n\n%s" % (note, commit_text)
    return system, user


def call_llm(system, user, model, base_url, api_key, timeout=120):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
    }
    req = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        eprint("error: 模型请求失败（HTTP %s）：%s" % (e.code, body))
        sys.exit(1)
    except urllib.error.URLError as e:
        eprint("error: 网络请求失败：%s" % e.reason)
        sys.exit(1)
    except (json.JSONDecodeError, TimeoutError) as e:
        eprint("error: 模型响应解析失败：%s" % e)
        sys.exit(1)
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        eprint("error: 模型返回结构异常。")
        sys.exit(1)


def get_api_key(cli_key):
    if cli_key:
        return cli_key
    key = os.environ.get("CHANGELOGGER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        eprint(
            "error: 未找到 API key。请设置环境变量 OPENAI_API_KEY（或 --api-key），"
            "或改用 --group 纯本地分组模式。"
        )
        sys.exit(1)
    return key


def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="changelogger",
        description="git log 进，release notes 出：把版本区间的提交交给 LLM 写发布说明。",
    )
    ap.add_argument("rev", nargs="?", default=None,
                    help="版本区间，如 v1.2.0..HEAD（默认：上一个 tag..HEAD，无 tag 则最近 20 条）")
    ap.add_argument("--group", action="store_true",
                    help="纯本地：按 conventional-commit 类型分组，不调用 LLM")
    ap.add_argument("--dry-run", action="store_true",
                    help="只展示收集到的提交和 prompt，不发起网络请求")
    ap.add_argument("--md", action="store_true", help="以 Markdown 输出（默认）")
    ap.add_argument("-o", "--output", default=None, help="把结果写入文件")
    ap.add_argument("--json", action="store_true", help="输出结构化 JSON（分组模式）")
    ap.add_argument("--lang", choices=["zh", "en"], default="zh", help="输出语言（默认 zh）")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="模型名（默认 %s）" % DEFAULT_MODEL)
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    ap.add_argument("--api-key", default=None, help="API key（默认读环境变量）")
    ap.add_argument("--cwd", default=".", help="git 仓库路径（默认当前目录）")
    ap.add_argument("--version", action="version", version="changelogger " + VERSION)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])
    cwd = os.path.abspath(args.cwd)
    ensure_git_repo(cwd)

    rev_args, label = resolve_range(args.rev, cwd)
    commits, truncated = collect_commits(rev_args, cwd)
    if not commits:
        eprint("error: 区间 %s 内没有提交，没有可写的发布说明。" % label)
        sys.exit(1)

    if args.group:
        groups = group_commits(commits, args.lang)
        if args.json:
            out = {
                "range": label,
                "truncated": truncated,
                "groups": {
                    k: [
                        {
                            "sha": c["sha"],
                            "author": c["author"],
                            "date": c["date"],
                            "subject": short_desc(c),
                            "breaking": is_breaking(c),
                        }
                        for c in v
                    ]
                    for k, v in groups.items()
                    if v
                },
            }
            result = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
        else:
            header = "# Release Notes（%s）\n\n" % label
            if truncated:
                header += "> 注意：提交数超过 %d，只收录了最新的 %d 条。\n\n" % (MAX_COMMITS, MAX_COMMITS)
            result = header + render_grouped(groups, args.lang)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(result)
        else:
            sys.stdout.write(result)
        return 0

    system, user = build_prompt(commits, args.lang, truncated)
    if args.dry_run:
        sys.stdout.write("===== 区间：%s（%d 个提交%s）=====\n\n" % (
            label, len(commits), "，已截断" if truncated else ""))
        sys.stdout.write("--- system ---\n" + system + "\n\n--- user ---\n" + user + "\n")
        return 0

    api_key = get_api_key(args.api_key)
    notes = call_llm(system, user, args.model, args.base_url, api_key)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(notes + "\n")
    else:
        sys.stdout.write(notes + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
