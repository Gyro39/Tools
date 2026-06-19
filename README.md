# BW Ticket Grabber

Bilibili World (BW) 周年票自动抢票脚本。直接请求 会员购 (show.bilibili.com) 接口，支持多人购票、预填信息、精准倒计时。

## 项目结构

```
BW ticket/
├── main.py                # 入口：python main.py [info|monitor|grab|check]
├── scheduler.py           # 精准倒计时器（亚秒级触发）
├── logger.py              # 结构化日志（控制台 + 文件）
├── bilibili/
│   ├── session.py         # 会话管理：Cookie 解析 + 反检测请求头
│   ├── sign.py            # Wbi 签名（api.bilibili.com 接口）
│   ├── member_buy.py      # 会员购 API：项目信息 / 下单 / 查单
│   └── order_flow.py      # 抢票流程编排 + 重试逻辑
├── config.example.json    # 配置模板
├── config.json            # 你的配置（gitignore）
└── requirements.txt       # 依赖：requests
```

## 配置

1. 复制 config.example.json -> config.json
2. 填写 config.json

### 配置说明

**project**
- `project_id`: BW 购票页 URL 中的数字
- `screen_id` / `sku_id`: 运行 `python main.py info` 查看可选票档后填写
- `ticket_count`: 购买张数

**buyers**: 购票人实名信息数组，人数必须等于 ticket_count
- `id_type`: 0=身份证, 1=护照, 2=港澳通行证, 3=台胞证

**account.cookie_string**: 浏览器登录后 F12 -> Application -> Cookies 获取，格式 `SESSDATA=xxx; bili_jct=yyy; buvid3=zzz; DedeUserID=www`

**schedule**
- `sale_time`: `YYYY-MM-DD HH:MM:SS` 格式，建议从官网确认精确时间
- `advance_ms`: 提前多少毫秒发起请求（200-500ms 为建议值）

**advanced**: 重试次数/间隔/UA/代理/超时

### Cookie 获取步骤

1. Chrome 打开 bilibili.com 并登录
2. F12 -> Application -> Cookies -> .bilibili.com
3. 找到以下 cookie 值并按格式拼接:
   `SESSDATA=xxx; bili_jct=yyy; buvid3=zzz; DedeUserID=www`

## 使用

```bash
# 安装依赖
pip install -r requirements.txt

# 查看项目信息（screen_id / sku_id）
python main.py info

# 环境和配置检查
python main.py check

# 监控倒计时（不实际抢票，用于测试时机）
python main.py monitor

# 全自动抢票
python main.py grab
```

## 流程

```
check/info             preflight          countdown            抢
config 验证     ->     登录、项目检查  ->  精确倒计时    ->   prepare -> create -> 结果
```

抢票分为两个阶段，均有自动重试：

1. **Prepare**: 下单预备，获取 order_token
2. **Create**: 用 order_token 创建真实订单

重试逻辑会识别「未开始」「限流」「售罄」「已结束」等不同返回，做出相应处理。

## 反检测措施

- 完整的浏览器请求头（Sec-Ch-Ua / Sec-Fetch-* / Accept-Language）
- Cookie 多域名设置（show.bilibili.com / .bilibili.com）
- 请求间隔随机抖动
- 指数退避重试
- CSRF token 自动注入
- Wbi 签名支持（api.bilibili.com 接口）

## 日志

运行后会自动在 `logs/` 目录生成详细日志文件，包含每次请求的完整响应，方便排查问题。

## 注意事项

- BW 票是热门票，建议服务器延迟低的环境运行
- advance_ms 不宜过大（>500ms），可能被风控
- Cookie 会过期，抢票前一天重新获取
- 票数必须和购票人数量一致
- 信息填错会直接抢票失败
