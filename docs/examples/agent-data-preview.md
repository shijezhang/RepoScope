# Agent 外发数据范围预览

状态：等待确认。Agent 尚未调用模型；两个固定流程已独立完成。

**接收服务：** `https://api.deepseek.com`；**模型：** `deepseek-v4-pro`。
本次仅请求允许将下列两个自建 fixture 的两侧源码、问题、符号/证据摘要、限制及测试摘要发送给该服务。
认证由现有配置完成；密钥不进入模型上下文、预览或结果文件。模型上下文不含配置内容、Click、HTTPX、RepoScope 业务源码或其他用户仓库。

配置文件不复制、不改写；本预览不含配置内容、真实密钥或配置文件路径。

预算：每个 Agent 任务总时限180秒，另有60秒Controller上限；最多6次决策、12次工具调用、12000 tokens、一次base/head验证；不自动扩额或重试超预算请求。

## 将发送的问题

> Review the supplied Python change and use the authorized base/head test comparison when it would resolve a meaningful uncertainty. State whether existing tests detect a change and preserve unresolved dynamic behavior. Do not equate passing tests with proof of safety.

## 初始摘要与后续工具范围

初始上下文包含run_id（排队时产生）、base/head SHA与snapshot ID、下列符号摘要、初始限制、plan_id/status、测试授权标志及空的prior_results。
后续仅发送当前fixture允许快照内的工具结果，最近3项结果回传模型；完整字段与实际ID见[JSON预览](agent-data-preview.json)。

- `find_symbol/search_code`：Symbols, relative paths, ranges, scores and evidence IDs exclusively in the two allowed snapshots of the current fixture.
- `get_neighbors/find_paths`：Graph edges/paths between existing symbols in those same snapshots; IDs, relation types, line positions and resolution status.
- `read_evidence`：One source excerpt from the three files shown below, with snapshot, path, range and content hash.
- `get_test_candidates`：The fixed plan ID, pytest nodeids, selection reasons, uncovered symbol IDs and coverage provenance for this fixture.
- `run_tests/get_test_result`：Plan status, selected count, comparable flag, up to ten non-passing findings, truncation flag and total result count; no credentials or provider settings.

## fixture-01

### 初始符号摘要

- base `calc.py:total`，距离0；附实际symbol/evidence ID。
- head `calc.py:total`，距离0；附实际symbol/evidence ID。
- base `api.py:<module>`，距离1；附实际symbol/evidence ID。
- head `api.py:<module>`，距离1；附实际symbol/evidence ID。
- base `api.py:order`，距离1；附实际symbol/evidence ID。
- head `api.py:order`，距离1；附实际symbol/evidence ID。

### 初始限制

- old: 4 unresolved/candidate call sites in affected files
- new: 4 unresolved/candidate call sites in affected files
- Tests have not been collected or executed for this plan
- No valid version-bound coverage imported; test selection must fall back conservatively

### 已完成固定流程的测试摘要示例

这些结果不预装入Agent；其调用run_tests后会独立产生同类摘要。

可比较：`True`；结论状态：`suspected_regression`。
- `test_calc.py::test_legacy`：base `passed` → head `failed`；`suspected_regression`。
- `test_calc.py::test_negative`：base `passed` → head `passed`；`passed_both`。
- `test_calc.py::test_plugin`：base `passed` → head `failed`；`suspected_regression`。
- `test_calc.py::test_positive`：base `passed` → head `failed`；`suspected_regression`。

### 完整源码（仅三个文件）

#### base `f68014c0a2919d82333ab6b2e5c9027af8773ea1`

**api.py**

```python
from calc import Base, legacy, total


def order(value):
    return total(value)


def old_order(value):
    return legacy(value)


def plugin(value):
    import calc

    return getattr(calc, "total")(value)


class Special(Base):
    def price(self, value):
        return super().price(value)
```

**calc.py**

```python
def total(amount):
    return amount if amount >= 0 else 0


def legacy(value):
    return total(value)


class Base:
    def price(self, value):
        return total(value)
```

**test_calc.py**

```python
from api import old_order, order, plugin


def test_positive():
    assert order(10) == 10


def test_negative():
    assert order(-2) == 0


def test_legacy():
    assert old_order(10) == 10


def test_plugin():
    assert plugin(10) == 10
```

#### head `31223fde0a33dd03278f31c4b93011c7c16ae192`

**api.py**

```python
from calc import Base, legacy, total


def order(value):
    return total(value)


def old_order(value):
    return legacy(value)


def plugin(value):
    import calc

    return getattr(calc, "total")(value)


class Special(Base):
    def price(self, value):
        return super().price(value)
```

**calc.py**

```python
def total(amount):
    return amount + 1 if amount >= 0 else 0


def legacy(value):
    return total(value)


class Base:
    def price(self, value):
        return total(value)
```

**test_calc.py**

```python
from api import old_order, order, plugin


def test_positive():
    assert order(10) == 10


def test_negative():
    assert order(-2) == 0


def test_legacy():
    assert old_order(10) == 10


def test_plugin():
    assert plugin(10) == 10
```


## fixture-04

### 初始符号摘要

- base `api.py:plugin`，距离0；附实际symbol/evidence ID。
- head `api.py:plugin`，距离0；附实际symbol/evidence ID。
- base `test_calc.py:<module>`，距离1；附实际symbol/evidence ID。
- head `test_calc.py:<module>`，距离1；附实际symbol/evidence ID。
- base `test_calc.py:test_plugin`，距离1；附实际symbol/evidence ID。
- head `test_calc.py:test_plugin`，距离1；附实际symbol/evidence ID。

### 初始限制

- old: 4 unresolved/candidate call sites in affected files
- new: 4 unresolved/candidate call sites in affected files
- Tests have not been collected or executed for this plan
- No valid version-bound coverage imported; test selection must fall back conservatively

### 已完成固定流程的测试摘要示例

这些结果不预装入Agent；其调用run_tests后会独立产生同类摘要。

可比较：`True`；结论状态：`inconclusive`。
- `test_calc.py::test_legacy`：base `passed` → head `passed`；`passed_both`。
- `test_calc.py::test_negative`：base `passed` → head `passed`；`passed_both`。
- `test_calc.py::test_plugin`：base `passed` → head `passed`；`passed_both`。
- `test_calc.py::test_positive`：base `passed` → head `passed`；`passed_both`。

### 完整源码（仅三个文件）

#### base `f68014c0a2919d82333ab6b2e5c9027af8773ea1`

**api.py**

```python
from calc import Base, legacy, total


def order(value):
    return total(value)


def old_order(value):
    return legacy(value)


def plugin(value):
    import calc

    return getattr(calc, "total")(value)


class Special(Base):
    def price(self, value):
        return super().price(value)
```

**calc.py**

```python
def total(amount):
    return amount if amount >= 0 else 0


def legacy(value):
    return total(value)


class Base:
    def price(self, value):
        return total(value)
```

**test_calc.py**

```python
from api import old_order, order, plugin


def test_positive():
    assert order(10) == 10


def test_negative():
    assert order(-2) == 0


def test_legacy():
    assert old_order(10) == 10


def test_plugin():
    assert plugin(10) == 10
```

#### head `aebd5c279a69fa36b4c7932b3949d84e31bca36d`

**api.py**

```python
from calc import Base, legacy, total


def order(value):
    return total(value)


def old_order(value):
    return legacy(value)


def plugin(value):
    import calc

    return getattr(calc, "legacy")(value)


class Special(Base):
    def price(self, value):
        return super().price(value)
```

**calc.py**

```python
def total(amount):
    return amount if amount >= 0 else 0


def legacy(value):
    return total(value)


class Base:
    def price(self, value):
        return total(value)
```

**test_calc.py**

```python
from api import old_order, order, plugin


def test_positive():
    assert order(10) == 10


def test_negative():
    assert order(-2) == 0


def test_legacy():
    assert old_order(10) == 10


def test_plugin():
    assert plugin(10) == 10
```

## 当前执行状态

自动审批审查拒绝了首次Agent命令，理由是服务使用授权尚未覆盖具体代码/数据向具体目的地外发。该命令没有发出模型请求。
本预览用于确认上述具体范围；确认前Agent保持暂停，已完成的固定流程不会重复执行。
