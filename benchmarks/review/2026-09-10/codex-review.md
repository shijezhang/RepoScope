# Codex 逐条复核 · 2026-09-10

已按用户要求完成12条源码与真实Docker复核。全部使用固定SHA；复核身份为Codex，原annotations.json仍保留unreviewed，不把机器复核提升为独立人工金标。

测试池：Click188项，HTTPX106项，fixture4项。每case本次仅一次Base/Head观察，失败保留为疑似回归；fixture-02/03为收集失败，未伪计为四个断言失败。

| 样例 | 源码判断 | Base | Head |
|---|---|---|---|
| click-01 | behavior-change | 188通过 | passed=183, failed=5 |
| click-02 | behavior-change | 188通过 | passed=183, failed=5 |
| click-03 | comment-only | 188通过 | passed=188 |
| httpx-01 | behavior-change | 106通过 | passed=105, failed=1 |
| httpx-02 | behavior-change | 106通过 | passed=105, failed=1 |
| httpx-03 | comment-only | 106通过 | passed=106 |
| fixture-01 | behavior-change | 4通过 | failed=3, passed=1 |
| fixture-02 | import-breakage | 4通过 | collection_failed |
| fixture-03 | import-breakage | 4通过 | collection_failed |
| fixture-04 | dynamic-target-change | 4通过 | passed=4 |
| fixture-05 | module-binding-addition | 4通过 | passed=4 |
| fixture-06 | behavior-change | 4通过 | passed=3, failed=1 |

关键区别：httpx-02失败的测试函数名是test_raise_for_status，但变异直接改变Response.is_error；不能把测试名称当作被测方法行为改变的证据。fixture-04当前返回值等价不代表动态替换场景全局等价；fixture-05新增可观察的模块属性，也不能当作注释变更。

## click-01

最小边界的开闭比较反转。clamp=False时，值等于min的接受/拒绝与原契约相反；最大边界分支未改。clamp=True须另分开闭边界审查，不能断言所有clamp输入都改变。

拟议影响：src/click/types.py:_NumberRangeBase.convert；src/click/types.py:IntRange；src/click/types.py:FloatRange

测试证据：passed=183, failed=5。

- 整数/浮点范围转换消费者可能受影响；不把整个模块都标为行为改变。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `934813e4d421071a1b3db3973c02fe2721359a6e`，Head `0ccf529f8dfa7b4c938a6f858840960661ee830d`。

## click-02

split_envvar_value改为单元素列表，停止按空白或指定分隔符拆分。例如foo bar由两项变为一项；空串原本可得到空列表，现得到含空串的列表。Parameter/Option的多值环境变量路径直接调用此方法。

拟议影响：src/click/types.py:ParamType.split_envvar_value；src/click/core.py:Parameter.value_from_envvar；src/click/core.py:Option.value_from_envvar

测试证据：passed=183, failed=5。

- 单值nargs=1且非multiple路径并非都调用拆分；不能据此断言所有参数解析受影响。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `934813e4d421071a1b3db3973c02fe2721359a6e`，Head `9cd90885b1a00c36d6e3957e5e9cb888de325e9e`。

## click-03

仅新增类内注释；去除位置属性的AST应完全相同，类docstring仍是首条语句。普通运行语义未变，源码位置、inspect/traceback等源码观察不在该无行为变化结论范围内。

拟议影响：普通运行语义下未发现行为变化。

测试证据：passed=188。

- 这是行为无影响候选；源码hash与行号变化仍真实存在。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `934813e4d421071a1b3db3973c02fe2721359a6e`，Head `14f39a8dc05482f50c6235814eca7e69076a7602`。

## httpx-01

codes.is_success(200)从True变为False，201–299判断未改。Response.is_success直接委托；Response.raise_for_status对已有request的HTTP 200不再提前返回，继续落到HTTPStatusError路径。

拟议影响：httpx/_status_codes.py:codes.is_success；httpx/_models.py:Response.is_success；httpx/_models.py:Response.raise_for_status

测试证据：passed=105, failed=1。

- 不能把所有2xx都判为改变；仅200边界改变。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `26d48e0634e6ee9cdc0533996db289ce4b430177`，Head `46707d46abf2c8794b816e46fd8326ba234cd005`。

## httpx-02

codes.is_error的下界400变成500，4xx被误判非错误；Response.is_error直接受影响。raise_for_status使用is_success而非is_error，这个变异不能直接证明它的行为改变。

拟议影响：httpx/_status_codes.py:codes.is_error；httpx/_models.py:Response.is_error

测试证据：passed=105, failed=1。

- 5xx判断保持；不把raise_for_status加入本变异的已确认行为影响。
- 失败测试虽然名为test_raise_for_status，内部包含response.is_error断言；测试名不能证明raise_for_status实现行为变化。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `26d48e0634e6ee9cdc0533996db289ce4b430177`，Head `261d24e05c7712da9d71c99a45eb954e8f994cf6`。

## httpx-03

只新增codes类内注释；忽略位置属性的AST应相同，枚举成员和类docstring不变。普通状态码判断行为未变。

拟议影响：普通运行语义下未发现行为变化。

测试证据：passed=106。

- 仍有源码位置变化，未覆盖源码反射场景。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `26d48e0634e6ee9cdc0533996db289ce4b430177`，Head `8d1823bad5381871301437aa4cbb402ec14992d0`。

## fixture-01

total对非负整数多加1，负整数仍0。order、legacy/old_order、Base.price/Special.price和plugin的返回路径都到达total；positive/legacy/plugin断言预期受影响，negative(-2)应保持。

拟议影响：calc.py:total；calc.py:legacy；calc.py:Base.price；api.py:order；api.py:old_order；api.py:Special.price；api.py:plugin

测试证据：failed=3, passed=1。

- plugin为源码可人工读出的动态路径；产品静态图仍必须保留unknown，不能伪造确定边。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `f68014c0a2919d82333ab6b2e5c9027af8773ea1`，Head `31223fde0a33dd03278f31c4b93011c7c16ae192`。

## fixture-02

删除legacy但api.py仍from calc import Base, legacy, total。正常导入api会因缺失legacy失败，test_calc导入api也失败；应记collection/import error，不能记成四条断言失败。calc.total和Base.price本身未改。

拟议影响：calc.py:legacy (base-only deleted)；api.py:<module>；test_calc.py:<module>

测试证据：collection_failed。

- 没有head侧legacy符号，删除证据只能引用base。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `f68014c0a2919d82333ab6b2e5c9027af8773ea1`，Head `1180c1492f7231f7f8e635a129f5a5375e16a2eb`。

## fixture-03

legacy改名renamed而api仍按旧名称导入，正常导入api及测试收集失败。新renamed函数保留原函数体，但没有为旧名称提供兼容别名。

拟议影响：calc.py:legacy (base-only)；calc.py:renamed (head-only)；api.py:<module>；test_calc.py:<module>

测试证据：collection_failed。

- 重命名不能靠相似函数体让旧导入自动有效。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `f68014c0a2919d82333ab6b2e5c9027af8773ea1`，Head `171cf7594744016cda32480db8852984c89feb61`。

## fixture-04

getattr目标由total变成legacy；当前legacy仅return total(value)，在现有正常调用和整数输入下返回值保持。因此结构/调用目标变化存在，但现有四测试无失败不等于所有动态环境安全。

拟议影响：api.py:plugin (structural target change)

测试证据：passed=4。

- monkeypatch、动态替换或反射观察可能区分两者；不把这个受限等价结论写成全局无影响。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `f68014c0a2919d82333ab6b2e5c9027af8773ea1`，Head `aebd5c279a69fa36b4c7932b3949d84e31bca36d`。

## fixture-05

新增模块属性FEATURE_FLAG=True，现有函数体和返回路径不变。外部读取该属性、dir或通配导入可观察到变化，故不能简单当作注释型无行为影响。

拟议影响：api.py:<module> (new FEATURE_FLAG binding)

测试证据：passed=4。

- 现有测试不验证该新增属性；通过不构成无变化证明。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `f68014c0a2919d82333ab6b2e5c9027af8773ea1`，Head `1fad98afbcd0c3c560c84b5745d44bb2321c48e5`。

## fixture-06

两处修改分别位于legacy与Base.price，均先value+1再调用total。对非负整数返回值增加1，old_order和Special.price经对应调用路径受影响；order和plugin仍直接调用未改的total。

拟议影响：calc.py:legacy；calc.py:Base.price；api.py:old_order；api.py:Special.price

测试证据：passed=3, failed=1。

- 现有四测试没有覆盖Base.price/Special.price分支；继承链的行为判断来自源码，不能冒称测试已验证。
- 本次单次执行不排除flaky；所选测试未覆盖的路径仍只依据源码。

输入：Base `f68014c0a2919d82333ab6b2e5c9027af8773ea1`，Head `7a63b7879ad729b8d64148b58b7aed212e1a8843`。

完整源码hash、diff hash和逐条观察见[codex-review.json](codex-review.json)；原始Docker结果见[执行记录](../../results/review-execution-run-9337ead858594201929459a903712032.json)。
