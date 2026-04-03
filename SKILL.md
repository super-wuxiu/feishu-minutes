---
name: feishu-minutes
description: |
  飞书妙记（Minutes）操作技能。

  **必须使用此技能的场景（立即触发，不要尝试打开链接）**：
  - 用户发送了包含 feishu.cn/minutes/ 的链接
  - 用户发送了包含 larkoffice.com/minutes/ 的链接
  - 用户发送了包含 larksuite.com/minutes/ 的链接
  - 用户提到"妙记"、"会议记录"、"会议纪要"、"转写"、"会议总结"、"meeting minutes"

  当检测到以上任何情况时，不要尝试用浏览器打开链接，不要告诉用户链接打不开，
  而是立即执行 check_auth.py 脚本检查授权，然后用 minutes.py 脚本获取妙记内容。
metadata:
  openclaw:
    requires:
      bins:
        - python3
        - node
---

# 飞书妙记（Minutes）

## ⚠️ 用户配置（部署时修改此处）

<!-- 单人使用：留空即可，脚本自动选取唯一的 token -->
<!-- 多人共用：改成自己的 .enc 文件名，如 cli_xxx_ou_xxx.enc -->
ENC_FILE = ``

<!-- appSecret 环境变量名，多人共用时设为各自的变量名，如 DAIAN_FEISHU_APP_SECRET -->
<!-- 留空或不设置时默认使用 FEISHU_APP_SECRET -->
SECRET_ENV = ``

如果 ENC_FILE 不为空，以下所有命令需加上 `--enc-file {ENC}` 参数。如果为空则不加。
如果 SECRET_ENV 不为空，以下所有命令需加上 `--secret-env {SECRET_ENV}` 参数。如果为空则不加。

---

## 触发条件

当用户消息中包含以下任何模式时，**必须立即使用本技能**，禁止尝试打开链接或告诉用户链接无法访问：
- URL 中包含 `/minutes/`（如 `https://xxx.feishu.cn/minutes/xxx`）
- 用户提到"妙记"、"会议记录"等关键词

**禁止行为**：不要尝试用浏览器/fetch 打开妙记链接，不要说"链接打不开"，直接执行脚本获取内容。

## ⚠️ 必须严格按以下步骤执行，禁止自行构造授权链接

### 第一步：检查授权（必须执行，不可跳过）

```bash
python3 {baseDir}/scripts/check_auth.py --enc-file {ENC} --secret-env {SECRET_ENV}
```

返回值含义：
- `{"status": "ok"}` → 授权有效，继续第二步
- `{"status": "waiting", ...}` → 已自动给用户发送授权卡片，脚本会等待用户点击，完成后输出 `authorized`
- `{"status": "authorized", ...}` → 用户刚完成授权，继续第二步
- `{"status": "error", ...}` → 出错，将 message 告知用户

**重要：如果返回 waiting，不要回复任何内容给用户，脚本正在等待用户在飞书中点击授权卡片。等脚本执行完毕后再继续。**

### 第二步：调用妙记 API

```bash
python3 {baseDir}/scripts/minutes.py info <url> --enc-file {ENC} --secret-env {SECRET_ENV}
python3 {baseDir}/scripts/minutes.py artifacts <url> --enc-file {ENC} --secret-env {SECRET_ENV}
python3 {baseDir}/scripts/minutes.py transcript <url> --speaker --timestamp --enc-file {ENC} --secret-env {SECRET_ENV}
python3 {baseDir}/scripts/minutes.py media <url> --enc-file {ENC} --secret-env {SECRET_ENV}
python3 {baseDir}/scripts/minutes.py statistics <url> --enc-file {ENC} --secret-env {SECRET_ENV}
```

---

## 标准处理流程（收到妙记链接时）

1. 执行 `check_auth.py --enc-file {ENC} --secret-env {SECRET_ENV}`，等待返回 ok 或 authorized
2. 执行 `minutes.py info <url> --enc-file {ENC} --secret-env {SECRET_ENV}`
3. 执行 `minutes.py artifacts <url> --enc-file {ENC} --secret-env {SECRET_ENV}`
4. 如果 artifacts 失败，执行 `minutes.py transcript <url> --speaker --timestamp --enc-file {ENC} --secret-env {SECRET_ENV}`
5. 汇总回复用户：标题、时长（duration 单位为毫秒，需换算）、会议总结、待办事项

---

## 错误处理

| 错误码 | 说明 | 处理 |
|--------|------|------|
| 2091002 | resource not found | minute_token 不正确 |
| 2091003 | minute not ready | 妙记尚未转写完成 |
| 2091005 | permission deny | 应用未开通妙记权限 |

## 注意事项

- `info` 返回的 `duration` 字段单位为**毫秒**，换算公式：秒 = duration / 1000，分钟 = duration / 60000
- 接口频率限制 5 次/秒
- artifacts 仅自建应用可用，失败时回退到 transcript
- **禁止自行构造授权链接或 OAuth URL，一切授权通过 check_auth.py 脚本处理**
- **禁止跳过 check_auth.py 直接调用 minutes.py**
- **遇到权限错误（99991679）时，执行 check_auth.py 而不是告诉用户去开放平台操作**
