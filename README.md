# feishu-minutes

飞书妙记（Feishu Minutes）OpenClaw 技能。自动获取飞书妙记的会议信息、文字记录、AI 总结等内容。

## 功能

- 获取妙记基本信息（标题、时长、创建者）
- 导出文字记录（支持说话人、时间戳、txt/srt 格式）
- 获取音视频下载链接
- 获取统计数据（PV/UV）
- 获取 AI 产物（总结、章节、待办）

## 安装

将 `feishu-minutes` 文件夹复制到 OpenClaw 的 skills 目录：

```bash
cp -r feishu-minutes ~/.openclaw/workspace/skills/
```

Docker 环境：

```bash
docker cp feishu-minutes <container>:/home/node/.openclaw/workspace/skills/
```

## 前提条件

- OpenClaw 已安装并运行
- 飞书 OpenClaw 官方插件（`@larksuite/openclaw-lark`）已安装且用户已完成基础授权
- 飞书应用已在开放平台开通妙记相关权限（**用户身份**和**应用身份**均需开通）：
  - `minutes:minutes` — 查看、创建、编辑及管理妙记文件
  - `minutes:minutes:readonly` — 查看妙记文件
  - `minutes:minutes.basic:read` — 获取妙记的基本信息
  - `minutes:minutes.transcript:export` — 导出妙记转写的文字内容
  - `minutes:minutes.media:export` — 下载妙记的音视频文件
  - `minutes:minutes.statistics:read` — 获取妙记的统计信息
  - `minutes:minutes.artifacts:read` — 获取妙记 AI 产物
- 容器内有 Python 3 和 Node.js

## 配置

编辑 `SKILL.md` 顶部的配置变量：

### ENC_FILE

指定 token store 中的 `.enc` 文件，支持文件名或完整绝对路径。

- **单人使用**：留空即可，脚本自动选取唯一的 token
- **多人共用**：改成自己的 `.enc` 文件名或完整路径

```
# 文件名模式（在默认 store 目录 ~/.local/share/openclaw-feishu-uat/ 中查找）
ENC_FILE = `cli_xxx_ou_xxx.enc`

# 完整路径模式（适用于不同用户主目录或自定义路径）
ENC_FILE = `/home/clouduser/.local/share/openclaw-feishu-uat/cli_xxx_ou_xxx.enc`
```

文件名格式为 `cli_{appId}_{userOpenId}.enc`。

默认 store 目录为 `~/.local/share/openclaw-feishu-uat/`（受环境变量 `XDG_DATA_HOME` 影响）。使用完整路径时，`master.key` 从 `.enc` 文件所在目录自动查找。

### SECRET_ENV

指定 appSecret 的环境变量名，用于多账号场景。

- **单人使用**：留空即可，默认使用 `FEISHU_APP_SECRET`
- **多人共用**：改成各自的变量名

```
SECRET_ENV = `DAIAN_FEISHU_APP_SECRET`
```

appSecret 的查找优先级：

1. 环境变量（使用 `SECRET_ENV` 指定的变量名）
2. `~/.openclaw/openclaw.json`（或 `openclaw.jsonc`）中匹配 `appId` 的配置
3. `~/.openclaw/.env` 中匹配 `SECRET_ENV` 变量名的值
4. `~/.openclaw/.env` 中的 `FEISHU_APP_SECRET`（兜底回退）

## 使用流程

为了确保权限正常获取，请按照以下步骤操作：

1. **触发基础授权**：首次使用时，请先在对话中发送一篇**你自己的飞书普通文档链接**。
2. **确认读取成功**：等待龙虾（OpenClaw）成功读取并回复该飞书文档的内容。这标志着基础的飞书用户授权已成功完成。
3. **发送妙记链接**：基础授权建立后，再发送你想要处理的**飞书妙记链接**。在这之后如果提示缺少专门的妙记操作权限，脚本会自动发送一张授权卡片提示你，点击卡片完成授权即可。

> **注意**：发送的妙记链接需要符合标准格式要求，例如：
> ```text
> https://xxx.feishu.cn/minutes/obcnrsia3n4kbd36v52a66b3
> ```

## 手动测试

```bash
# 检查授权
python3 scripts/check_auth.py

# 检查授权（多用户 + 自定义 secret）
python3 scripts/check_auth.py \
  --enc-file /home/clouduser/.local/share/openclaw-feishu-uat/cli_xxx_ou_xxx.enc \
  --secret-env DAIAN_FEISHU_APP_SECRET

# 获取妙记信息
python3 scripts/minutes.py info <minute_token_or_url>

# 获取文字记录
python3 scripts/minutes.py transcript <minute_token_or_url> --speaker --timestamp

# 获取 AI 产物
python3 scripts/minutes.py artifacts <minute_token_or_url>

# 完整参数示例
python3 scripts/minutes.py info <minute_token_or_url> \
  --enc-file cli_xxx_ou_xxx.enc \
  --secret-env DAIAN_FEISHU_APP_SECRET
```

## License

MIT
