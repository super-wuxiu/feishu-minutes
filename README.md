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
- 飞书应用已在开放平台开通妙记相关权限（`minutes:minutes:readonly`、`minutes:minutes.transcript:export`）
- 容器内有 Python 3 和 Node.js

## 配置

编辑 `SKILL.md` 顶部的 `ENC_FILE` 变量：

- **单人使用**：留空即可，脚本自动选取唯一的 token
- **多人共用**：改成自己的 `.enc` 文件名

```
ENC_FILE = `cli_xxx_ou_xxx.enc`
```

文件名格式为 `cli_{appId}_{userOpenId}.enc`，位于 `~/.local/share/openclaw-feishu-uat/` 目录下。

## 使用

在飞书中发送妙记链接即可自动触发，例如：

```
https://xxx.feishu.cn/minutes/obcnrsia3n4kbd36v52a66b3
```

首次使用时如果缺少妙记权限，脚本会自动发送授权卡片给用户，点击授权后即可使用。

## 手动测试

```bash
# 检查授权
python3 scripts/check_auth.py

# 获取妙记信息
python3 scripts/minutes.py info <minute_token_or_url>

# 获取文字记录
python3 scripts/minutes.py transcript <minute_token_or_url> --speaker --timestamp

# 获取 AI 产物
python3 scripts/minutes.py artifacts <minute_token_or_url>
```

## License

MIT
