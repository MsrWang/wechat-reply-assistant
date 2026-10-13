# 开发说明

本仓库是“微信回复助手（Jev）”的独立源码项目。后续修改、提交与同步都在本仓库进行，不要继续修改旧任务中的源码副本。

## Windows 上从源码运行

要求 Windows x64、Python 3.11、Git。真实微信功能当前面向 Windows 11 / WeChat Windows 4.x；其他版本需要单独验证。以下命令在项目根目录的 PowerShell 中执行。

首次准备环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
```

安装会访问 Python 包源；锁文件固定版本和 SHA-256，不需要 OpenRouter Key。不要改用未锁定的 `requirements.txt` 作为可复现发布依据。如果锁定的包不可下载，应调查原因后更新锁文件，不能静默换版本。

启动：

```powershell
.\.venv\Scripts\python.exe main.py
```

先使用界面的“示例体验”：它不联网、不需要微信或 Key，也不能填入微信。真实分析需要你在设置中提供自己的 OpenRouter Key；不要把 Key 写进源码、GitHub 或 `.env`。勾选保存后使用 Windows 凭据管理器；本仓库不会同步其他电脑上的凭据。

源码启动会使用当前 Windows 用户已有的 Jev 偏好和凭据目标。它不自动迁移、删除或覆盖桌面安装版；如需完全隔离的体验，应使用独立 Windows 用户并另行设置 Key。不要同时运行源码版与桌面版，单实例保护会阻止重复实例。

## 测试

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

完整回归应在原生 Windows Python 中进行。Qt 测试使用离屏模式，捕获、OCR、Provider、凭据和填入边界使用替身；发布脚本测试在临时目录内运行。测试无需真实 Key，也不应操作真实聊天。

WSL/Linux 只能验证不依赖 Windows 的部分；Windows Qt 或系统工具测试可能跳过。报告必须区分通过数、跳过数和测试环境，不能把跳过当作通过。

自动化测试通过不等于真实回复质量、物理遮挡 OCR 或用户体验已验收。涉及微信填入的改动仍须单独观察，并保持用户手动发送。

## 构建 Windows 文件夹版

首次准备独立构建环境：

```powershell
py -3.11 -m venv .build-venv
.\.build-venv\Scripts\python.exe -m pip install --require-hashes -r requirements-build.lock
```

先提交并保持 Git 工作区干净，再检查计划和构建：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build-release.ps1 -PlanOnly
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\build-release.ps1
```

输出为 `dist\releases\<版本>\jev-chat-windows\`，版本来自 `app/version.py`。完整目录必须一起保留；脚本拒绝覆盖已有版本目录。构建不会临时联网安装依赖。后续功能发布请更新版本号以及相关测试/文档，再构建新目录。

交付前核对包内 `release-manifest.json`、文件哈希、包内启动、精确目录安全扫描和正常窗口启动。不要直接双击历史 `Install-Jev.cmd` 代替这一流程，它仍绑定 0.1.2。打包和扫描通过不能替代目标电脑的实际使用验收。

## 持续优化与同步

每次围绕一个实际问题改进：复现问题，调整代码，运行相关测试及完整回归，再提交。建议用短小的功能分支，避免把未验证的实验和日常可用版本混在一起。

```powershell
git switch -c feature/short-description
# 完成修改与验证后：
git add <需要提交的文件>
git commit -m "Describe the change"
git push -u origin feature/short-description
```

本仓库公开可见。不要上传配置、凭据、真实聊天、运行日志、截图、评估请求原文、个人路径、内部记录或虚拟环境。`.gitignore` 只是第一道保护；提交前仍需查看 `git diff --cached`、文件清单和提交历史。参与方式见根目录 `CONTRIBUTING.md`。

## 主要代码入口

- `main.py`：用户动作、工作线程、结果生命周期和填入控制。
- `app/overlay.py`、`app/reply_editor.py`、`app/conversation_timeline.py`：桌面交互、草稿编辑和对话显示。
- `app/capture.py`、`app/ocr.py`、`app/fill_target.py`、`app/freshness.py`：手动读取、OCR、目标与内容复核。
- `core/draft.py`、`core/jev_client.py`、`core/adaptive_analysis.py`：起草、Jev 判断和结构化解析。
- `app/settings.py`、`app/credentials.py`：普通偏好与 Windows 凭据管理，不能保存聊天或把 Key 降级为明文。
- `tests/`：纯逻辑、控制器、原生 Qt 合成流程和发布工具测试。

每次提交都应说明当前验证结果，不把旧测试结论继承为新版本的验收。测试与普通开发不需要真实聊天或 Key。

离线评估所需历史基准固定在 `scripts/fixtures/` 并核对 SHA-256，不再依赖旧任务的 Git 提交。普通测试不执行收费评估；不要把 `scripts/evaluate_replies.py --run` 当作测试命令，它会读取本机 Key 并发起真实请求。
