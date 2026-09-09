# RepoScope overnight：2026-09-10

用户授权：继续推进剩余工作，早上同步。时区Asia/Shanghai，08:00停止新增长实验，08:30晨报。
当前开发分支：zhangshijie/reposcope-upgrade；启动基线779834c，66项测试通过、GitHub CI通过。
当前任务唯一heartbeat：reposcope-overnight（夜间续跑和晨报共用，不创建重复调度）。

## 执行顺序

1. Agent上下文预算打包：消费已保存的失败，保留单任务6决策/12工具/12000 tokens/180秒总预算及60秒controller边界。优先在预算内按完整证据单元选择上下文，不能靠加大预算掩盖问题。
2. 结构化输出诊断：保留finish_reason、字段级错误、请求用量等非敏感元数据；不保存密钥/请求头或模型内部推理，不猜测旧失败细因，不无限重试。
3. 修复后用单独的新实验记录验证，原run-68deaa06d5cd4072892470a1d8740b3d的失败不可覆盖或伪改完成。固定与Agent相同输入和最高预算，保留无收益。
4. 独立推进长函数分块、检索/解析一致性与测试选择工程对照。正式评测先准备可复核标注，不把模型生成标签直接标成reviewed。
5. 每项改动完成相称单测/集成测试；更新交付状态、技术报告和实际结果；提交当前分支、检查CI。不要合并main。

## 授权与环境

- 外发仅限docs/examples/agent-data-preview.json所列fixture-01/fixture-04两侧源码及图/测试摘要；DeepSeek目的地和模型不得变化。原预览JSON已封存，不修改它。
- 用户配置位置沿用当前任务已提供的本地文件；启动指针保存在忽略的artifacts/runtime.env。不得把配置内容或密钥复制进仓库/报告。
- 本次overnight授权继续修复与相称验证；每个新实验仍遵守既有单任务预算，不能偷偷重提已终止任务、无上限重复试到成功。遇审批拒绝记录实际原因，继续其他可做工作。
- Docker/Colima及fixture、Click、HTTPX测试profiles已安装；模型环境artifacts/environments/models、冻结模型manifest可复用。
- 保留不可变benchmark run目录与历史Git对象。不要为刷新实验删除旧仓库。

## 每轮检查点格式

写明时间、目标、改动文件、执行命令与结果、实验ID、commit/CI、阻塞及下一步。避免只写计划而没有可审查产物。

## 晨报

08:30在当前任务同步完成项、验证数字、提交和报告链接、失败/未完成项；明确是否真正改善Agent验证。报告保存docs/overnight-2026-09-10-report.md。完成后在本文件记录morning_report_sent=true，停止本夜调度，不重复通知。

## 检查点

### 02:00启动

- 已核对工作区干净、基线与现有负面结果。
- 已安排每半小时唤醒至08:30，08:30由同一heartbeat发送晨报。
- 首要缺口：保守UTF-8字节预算提前阻止模型；输出校验未保存finish_reason/字段级错误。
- morning_report_sent=false

### 启动轮：第一项实现

- 已补Provider非敏感诊断：finish_reason、输出字符数、结构校验错误类型与字段位置、HTTP状态/异常类型；不会保存原始模型输出、内部推理、密钥或请求头。
- Controller将诊断写入报告metadata，便于下一轮真实探针定位校验失败。
- 新增截断响应测试；Provider与核心定向测试通过24项。
- 下一步：在既有token预算内按完整证据打包context，补预算边界测试，再做独立新实验；不覆盖原负面记录。
- 本机已启动临时caffeinate，启动后6小时40分钟自动退出；仍需保持Codex应用运行和网络可用。

- 常驻API/worker尚未因本轮修改重启；晨前完成代码后重启并核验本机health与真实界面。

### 02:30轮：完整证据预算打包与真实验证

- 新增llm/messages.py统一实际请求序列化与预算计算；输出仍600 tokens，累计仍12000，另保留512 framing余量。按实际system/context UTF-8上界而非重复估算。
- 新增agent/context.py：按完整结果/限制/符号摘要准入，优先最新工具反馈，遗漏明确计数；不切断源码或签名。
- 保存原失败作为回归测试，在原fixture-01停止点相同剩余预算下可容纳完整最近证据。
- 真实新实验run-94512b88b97e4d068473d9da5008ee31，结果agent-night-0230.json；继承原审批scope并运行前后核验，原agent-validation.json未变。
- fixture-01 Agent：3次请求，5929输入/1278输出，28.18秒；实际触发run_tests并生成一组Base/Head结果（改进：原来没有验证）。但下一轮仍因剩余上下文预算不足停止，没有模型再次消费测试反馈。
- fixture-04 Agent：3次请求，5847输入/1598输出，27.63秒，未触发测试。现在有明确诊断：finish_reason=length、content_characters=0、json_invalid；只能对本次失败认定生成上限耗尽且JSON为空，不反推旧失败原因。
- 同轮固定流程分别6.98/6.73秒完成测试。不能宣称Agent更快或正式质量收益。
- 71项全量pytest通过、ruff check/format与diff-check通过；新实验不覆盖旧失败。
- 下一轮：处理已耗尽的工具预算与重复schema开销（run_tests执行后不应仍向模型提供可再次执行的schema）；研究明确的结构化短决策输出配置，保持模型名称、输出/总预算和数据范围不变，保留本次负面结果。不要盲目增加max_tokens或重复请求。

- 本轮核心提交：1f9af01；已推送，[CI通过](https://github.com/shijezhang/RepoScope/actions/runs/34391095267)。原始失败文件SHA256仍为699c9f0a6cd9f82099a7d4324a28dbb60e7f77009d9cc8f5d848028a931ccdff。

### 03:00轮：结构化短决策、真实反馈收尾与分块基础

- 核对官方DeepSeek thinking参数，新增显式可选disabled/enabled，仅支持指定DeepSeek v4服务；未改用户配置文件、模型名、600输出或12000总预算。默认请求行为不变。
- 03:00独立实验run-3efbebc3f3a044a5b052a879e9934a7d（agent-night-0300.json）仍失败：一例重复get_test_candidates；一例明确缺少summary，不再是截断。保留结果。
- 修复真实工具职责说明和完整JSON示例；候选ID向模型有界摘要但执行计划完整；run_tests耗尽后移出可用schema，后端防重仍保留。
- 03:15新实验run-17da207b8d9b48f4a60f5c6d794cd78b（agent-night-0315.json）：两Agent均实际触发一次Base/Head，模型消费测试反馈后model_finished，无字段错误。fixture-01观察3条疑似回归，fixture-04保持passed_both与动态限制。
- 两Agent分别11.35/10.52秒、3/4请求、6353/7679总tokens；同轮fixed分别5.60/4.60秒。模型更慢，不能宣称收益。所有上下文准入、数据范围和每任务预算已核验，未覆盖旧实验。
- 独立子任务新增indexing/chunks.py与22项测试：完整行、完整签名/声明、范围/content_hash校验、稳定chunk ID、超限明确partial。主Agent已读代码并统一验证。
- 全量96项pytest通过，ruff check/format、diff-check通过。分块暂未接Search，下一轮接真实tokenizer并验证chunk到parent聚合。
- 下一轮优先：接线长函数分块；强检索实验仍只本地公开仓库，不向模型API扩大源码范围。可选将已验证短决策配置用于本地工作台，需晨前再做最终UI/打包/Compose核对。

- 本轮提交f98e1d2已推送；CI请下一轮核对。工作台、wheel、Compose仍须晨前按最终源码重新核验，不沿用旧source_matches_current标记。

### 03:30轮：长函数分块接入强检索

- 新增retrieval/corpus.py；Search强检索用实际Embedding/CrossEncoder tokenizer无截断计数，按保留query/pair空间的预算构建ready块；超限query拒绝，超限源码保留partial统计不送模型。
- Dense先按chunk再聚合父符号，最多两个候选块重排，每父只返回一项且附可验证chunk源码/行/hash；所有pair在模型调用前复核预算。缓存绑定chunk内容/父映射/分块和模型配置，不复用旧整符号缓存。
- 7项新假模型集成测试通过，覆盖实际输入预算、后段命中、父级去重、partial/全超限、query预算与cache；主Agent已审查分块实现和全部结果。
- 真实本地重模型4查询和2次磁盘重载通过，排名相同/分数差0。Click3258 ready/75 oversized，完全覆盖1422/1452父；HTTPX2802/85，1251/1301父。两语料均partial，质量无金标，部分首位仍为模块/测试。
- 首次总时长97.11/51.80秒，warm0.51/0.61秒；重载18.11/16.03秒。新结果strong-retrieval-chunks-0330.json，旧强检索记录未覆盖。所有推理在本地CPU，没有API外发。
- 下一轮优先M5：实际模型tokenizer/config文件也应核验并纳入cache binding（当前只强校验weights，revision/hash声明还不足以发现本地tokenizer被改动）；然后设计统一发布边界。勿把当前vector artifact ready误称全语料ready。
- Corpus重建当前仍较慢，可先持久化可验证chunk/稀疏派生产物，再用bundle同时引用graph/chunks/vector；不要为了速度跳过内容/预算验证。

- 本轮103项全量pytest通过，源码提交3868a23已推送；下一轮核对CI。计数阶段原始日志保留artifacts/model-probe/chunk-probe-0330.log，临时路径已清理。

### 04:00轮：实际模型文件完整性

- 新增retrieval/model_files.py，每次强检索核验全部本地模型文件，已声明清单时严格匹配文件集合/hash/大小，拒绝修改、增删和符号链接。旧配置未声明完整清单时仅称local-content-fingerprint。
- 实际文件身份纳入内存运行时key和磁盘vector binding；相同revision下更换配置也不复用旧语料/向量。每次warm query也核验，防止已有进程漏检本地变更。
- 新增6项集成测试，109项全量pytest通过；ruff check/format、diff-check通过。真实embedding11文件/reranker7文件全部核验成功，流式hash耗时0.194秒；新warm延迟应计入此成本，未重用旧时延结论。
- 结果model-file-verification-0400.json；边界记录ADR009。未调用模型API，未修改审批预览或旧实验。
- 已确认3868a23与5d38129的CI通过。本轮提交后下轮核对新CI。
- 下一轮：持久化hash校验的chunk/text/sparse语料以减少16秒重载成本，然后围绕固定snapshot/graph/corpus/vector/test目录建立统一发布指针，失败不得替换已发布版本。当前尚未实现此联合发布，不要在交付表上提前勾选。
- 晨前仍需最终wheel、Compose、API/worker重启和真实UI版本核验；这些源匹配状态保持false。

### 04:30轮：强检索语料与向量原子bundle

- 已确认087af47 CI通过。新增retrieval/index_bundle.py，VectorCache支持同一NPZ中的snapshot/chunks/sparse terms/stats/vectors分量校验；Search复读不再重新tokenizer分块。绑定实际模型文件、语义snapshot、策略与库版本。
- 恢复时重建源码声明/context核对完整证据与chunk ID，检查行范围/hash、预算、稀疏词项、向量行数；partial状态原样保留。推理/重排失败不发布新bundle，旧版本仍可读。
- 新增5项集成覆盖缓存恢复不重新分块、源码篡改（含重算外层hash）、重排失败不发布、graph变化与stats变化区别。最初新增测试把模块源码误声明为Function，修正fixture后114项全量pytest通过；ruff check/format和diff-check通过。
- 真实公开仓库新实验strong-retrieval-bundle-0430.json：四查询/两次磁盘复读成功，排名相同、分数差0。Click/HTTPX复读2.05/1.72秒（此前18.11/16.03），冷构建100.88/53.53秒仍未改善，两语料仍partial。
- 在恢复声明/context校验补全后，用最终源码单独复读已有缓存，记录strong-retrieval-bundle-recheck-0430.json；四查询/两次复读再次成功，无重新embedding或外发。原始日志保存在artifacts/model-probe。
- ADR010明确：完成的是强检索内部共同发布。SQLite图快照、产品默认弱检索与运行时pytest目录仍独立，尚未完成全部组件的产品默认发布指针；不能提前标全局联合发布完成。
- 下一轮：接产品Store/查询profile的发布边界，失败不得替换已发布版本；或推进待人工复核候选与真实覆盖选择对照。晨前预留足够时间重建wheel/Compose、重启API/worker并核验真实UI。
