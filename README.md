# Baseline Hub

面向普通用户的独立 Vue 前端和轻量 API。连接已有 AutoTune 后端的 `hub` 插件，不要求 execution API patch。

- 用现有 AutoTune 账号登录；开放注册时也可在登录页创建 AutoTune 账号并自动登录。Hub 保存自己的会话和后端账号关联。
- 查询赛道列表、详情、规则、已验证镜像、榜单及提交记录。
- 选择镜像，提交运行命令或参数，跟踪状态与测量结果。
- 复用原有赛道卡片、榜单比较、参数编辑、历史配置复制、自己的提交迭代与取消、运行日志。
- 生成与预览报告、查看生成任务及重试、查看保存的报告并下载 HTML / ZIP。
- 预览 Model Catalog 配方和文件、选择环境变量分类。
- 绑定 GitHub，之后可用 GitHub 登录同一 Hub 身份。

模型启动、排队、GPU 调度、测量和报告生成由 AutoTune 负责。报告功能需要后端已启用 autoreport 并配置生成服务。

赛道导出到 Catalog 时，只能选择一个 baseline 和一个 opt；管理员已指定 baseline 时不可修改。opt 只能选择当前用户提交的结果；未指定管理员 baseline 时，可以选择当前赛道榜单中的任意结果作为 baseline。后端在预览、下载和发布提交时逐项校验运行归属。选好运行后，手动点击“生成报告并预览”。Hub 优先复用 baseline 和 opt 运行 ID 完全匹配的已有报告；没有报告时复用正在生成的对应任务，或创建新任务。报告完成后才允许下载或创建 PR，报告 HTML 随配置文件一起提交。

## 本地启动

需要 Python 3.12+、Node.js 22.18+（包含原生 TypeScript 测试支持）。

```sh
cd baseline-hub/backend
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

将生成的 key 填入 `.env` 的 `HUB_CREDENTIAL_KEY`。该密钥加密服务端存储的 AutoTune token，重启时保留同一个值。不要提交 `.env`。

示例已经配置：

```dotenv
HUB_UPSTREAM_URL=http://172.28.4.5:30522
HUB_UPSTREAM_PROXY=socks5h://127.0.0.1:8991
HUB_PUBLIC_URL=http://localhost:5173
HUB_COOKIE_SECURE=false
```

8991 是 SSH SOCKS 代理，不是 AutoTune 服务端口；base URL 必须填写实际后端地址。部署环境可直连时将 `HUB_UPSTREAM_PROXY` 留空。代理设置只作用于 AutoTune 请求。

```sh
# backend/ 目录，环境读取自 .env
.venv/bin/alembic upgrade head
.venv/bin/uvicorn baseline_hub.main:create_app --factory --host 127.0.0.1 --port 8000
```

另开终端：

```sh
cd baseline-hub/frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

打开 [本地 Hub](http://localhost:5173)，使用已有 AutoTune 用户名和密码登录。注册入口跟随远端 AutoTune 的 `/api/auth/options` 中 `signup` 配置；关闭注册时请联系平台管理员。注册通过远端 `/api/auth/register` 完成，不创建独立的 Hub 密码账号。

无需另建 Hub 管理员；执行和调度由 AutoTune 负责。

## 测试 GitHub 绑定

1. 在 GitHub 创建 OAuth App。开发时 Homepage URL 填 `http://localhost:5173`，Authorization callback URL 填 `http://localhost:5173/api/auth/github/callback`。
2. 将 Client ID、Client Secret 填入后端 `.env` 的 `HUB_GITHUB_CLIENT_ID`、`HUB_GITHUB_CLIENT_SECRET`，重启 Hub API。
3. 用 AutoTune 账号登录 Hub，进入“账号与 GitHub”，点击“绑定 GitHub”并完成授权。回到账号页应显示绑定的 GitHub 用户名。
4. 退出后可使用 GitHub 登录。GitHub 新会话不会继承之前的 AutoTune token，需要在账号页重新连接原 AutoTune 账号后查询赛道。

账号关联使用后端 URL + 后端数字 user ID；GitHub 关联使用 GitHub 数字 ID，不通过邮箱或同名账号自动合并。后端密码只用于登录请求，不落库；token 按 Hub 会话加密保存，退出即删除。后端认证过期只清理对应 token，保留 Hub 会话及 GitHub 绑定。

未配置 OAuth App 时会显示配置提示，不能进行真实 GitHub 绑定。自动化测试使用模拟 GitHub HTTP 响应覆盖成功、取消、冲突、重复回调和会话校验。

## Compose

在 `baseline-hub/deploy/` 复制 `.env.example` 为 `.env` 并填写数据库密码、密钥和 public URL 后：

```sh
docker compose -f compose.yml up -d --build
```

默认启动 PostgreSQL、迁移、API、publisher 和前端。容器内的 `127.0.0.1` 是容器自身，Docker Desktop 的本机 SOCKS 代理可填写 `socks5h://host.docker.internal:8991`；Linux 需要设置可达的宿主机地址。生产使用实际 HTTPS public URL，并保留 `HUB_COOKIE_SECURE=true`。

## 验证

```sh
cd baseline-hub/backend
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
cd ../frontend
npm test
npm run build
# Vite 和 API 已启动后，检查实际开发服务是否加载组件插件
npm run test:dev-ui
```

当前仅支持连接 AutoTune 的运行方式。

## Catalog 自动 PR

1. 在账号页绑定 GitHub，并点击“授权 GitHub 发布”。登录授权和发布授权分开，发布凭证加密保存在 Hub。
2. 在赛道或测量详情页打开 Catalog 导出，选择自己的测量结果，可同时选择公共 baseline。补齐模型信息和需要附带的报告。
3. 点击“预览 GitHub diff”，核对目标仓库、文件、报告及 PR 描述，再点击“确认变更并创建 PR”。修改表单后必须重新预览。
4. 后台自动 fork、创建分支、单次提交并创建 PR，页面显示 PR 链接。关闭页面不会取消任务，在账号页“Catalog 发布历史”可找回、查看或重试。

`HUB_CATALOG_REPOSITORY` 默认是 `PsLink/model-catalog`。可改为 `modelsphere/model-catalog` 或其他兼容目录仓库。每个任务冻结预览时的目标仓库和 commit，修改配置不改变已有任务。索引只服务普通导出预览，发布 diff 以目标仓库当前内容为准；目标分支变化时需重新预览。

Compose 默认启动 `publisher` 服务。直接运行开发服务器时，还需在与 API 相同的数据库、密钥和环境配置下启动：

```bash
cd baseline-hub/backend
.venv/bin/python -m baseline_hub.publishing
```

GitHub 失效授权会要求重新授权；限流或网络错误自动退避重试。重复点击、PR 响应丢失及后台重启复用同一任务和分支。撤销本站发布授权会删除 Hub 保存的发布 token，后续任务需重新授权；已创建的 PR 保留。


## 后端用户操作模块

`api.py` 和 `ui_api.py` 是用户 HTTP 入口，负责路由白名单、会话及响应适配，入口之间不互相调用。
`submission_commands.py` 集中管理提交字段、赛道和镜像校验、归属检查、重投及停止运行；修改提交规则时在此维护。
`catalog_exports.py` 将报告比较校验、远端导出和文件过滤组合为预览及 ZIP 操作，普通导出和发布文件准备共用。
`upstream.py` 负责远端 HTTP 与认证失效处理，创建任务响应丢失时明确返回结果不确定，不自动重试写请求。

业务模块测试直接使用模拟 HTTP 验证规则；用户入口测试验证 CSRF、权限、响应状态和二进制下载契约。报告附件的 MIME 与签名检查保留在响应适配层，数据库迁移历史及旧表映射保持兼容。

`catalog_lookup.py` 维护目标仓库的模型查找和不可变内容缓存。目录树按 tree SHA、文件及解析结果按 blob SHA 复用；模型查找结果随目录树变化重新计算，返回数据为副本。缓存按仓库和凭证摘要隔离，并限制为每进程最多 8192 项及约 32 MiB 的对象预算；并发读取同一内容共用一次请求，失败读取和超出预算的内容不保留。

每次预览仍重新获取仓库权限和分支 HEAD，publisher 写入前的基准 commit 检查保持不变。缓存只存在于当前进程，重启后重新预热；首次按 Hugging Face 身份查找未知模型时仍可能读取多个模型的 metadata，不依赖额外远端索引或数据库迁移。
