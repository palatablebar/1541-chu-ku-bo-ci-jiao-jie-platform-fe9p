# WAVE DESK运行手册

更换班次或复算派单时使用。先确认 `/health` 返回 `ok`，再检查以下入口和凭证中的 `run_id`。

| 文档或路由 | 用途 |
|---|---|
| `sample_data/pick_manifests_sample.json` | 同时提供拟合、校准、回放和当班的样例 |
| `GET /api/waveclerk/overview` | 默认额度和样例处置 |
| `POST /api/waveclerk/run` | 用 `capacity` 和 `strategy` 调整派单，也可同时提供 `records` 与 `as_of` |
| `POST /api/waveclerk/reinforce` | 用 `flex_workers` 出当班增援单，可同时提供 `records` 与 `as_of` |
| `GET /api/waveclerk/stages/<环节>` | 样例的分环节核对，可传相同额度、策略 |

导入 JSON 对象时 `records` 为 1–2500 条单据，`as_of` 为整数工日；默认第 96 日。额度接受 1–60，策略接受 `balanced` 或 `focus`。入站异常保留于第一环节。整批缺少成熟窗口时响应 422 并给出原因。

增援单的 `flex_workers` 接受 1–30 的整数机动人数，沿用同一批数据冻结出的回归与校准，不改动 `/run` 的处置结论。响应含每张单的分档增员推演（`options`）、人手守恒账（`ledger`）和对账凭证（`reconciliation`、`stamp`）。同批数据与相同机动人数重跑，凭证戳与派人名单一致。

```sh
docker compose exec -T app python -m waveclerk.entry evaluate --input sample_data/pick_manifests_sample.json --capacity 9
docker compose exec -T app python -m waveclerk.entry reinforce --flex 8 --input sample_data/pick_manifests_sample.json
```

在网页筛选单号、作业区或原因，点击结果中的单号查看完整字段与处置证据。下载 JSON 可检视结果、对照原始资料摘要和模型特征版本。
