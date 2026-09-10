# 开发样本人工复核入口

`2026-09-10/` 包含12条原有开发变异的固定Git diff、SHA、diff hash和待填写annotations.json。它们仍是unreviewed，尚无正式金标，不足以形成120条测试集或holdout。包中没有系统预测和原始变异假设，避免直接复制预测作为标签。

先在记录的replay_repository中核对base_sha/head_sha与diff_sha256，阅读两侧源码及调用者，独立填写gold_impacts和逐项证据。测试判断另需固定Docker profile、完整已登记test_pool及config_hash，区分单次观察与稳定失败；不要用null冒充“没有影响/没有失败”。reviewer、reviewed_at和证据必须由实际复核者填写，完成后才可更新annotation_status。

origin_group保守地按repo与base分组；所有当前样本都留在development。同来源变更不能移入不同split制造独立测试集。正式test需新增独立来源、冻结后再运行方案对照。

重新导出使用新目录，工具拒绝覆盖已有复核工作：

```bash
uv run --frozen python -m benchmarks.runners.review_packet --output benchmarks/review/NEW_REVIEW_ID
```

`benchmarks/runners/metrics.py`仍拒绝本包；不能为了让评分运行而批量将unreviewed改为reviewed。

用户后续指定由Codex复核；现已完成[逐条复核报告](2026-09-10/codex-review.md)与12条真实Docker对照。原始空白标注不覆盖，机器复核结果保存在单独文件。
