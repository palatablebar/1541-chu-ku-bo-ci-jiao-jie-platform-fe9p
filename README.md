# 出库波次交接台

日配仓拣货主管用这套工作台处理波次完工时长回归与通道分派。进入工作台可调整本班复核额度，查看每张拣货波次的预测、命中规则和处置，导出单班可复算记录。

## 本地运行

```sh
docker compose up -d --build
```

访问 `http://127.0.0.1:18640`。样例是可复算的模拟班次，涵盖正常历史、较迟回执、最新冲突版、缺测单和临时分区。主管还可在工作台填报机动人数，生成当班增援单：只给原本赶不上、增员后完工区间上沿回到装车缓冲内的单派人，缺测或超训练域的单留人工。

```sh
docker compose exec -T app python -m waveclerk.entry evaluate --capacity 8 --strategy focus
docker compose exec -T app python -m waveclerk.entry reinforce --flex 8
docker compose exec -T app pytest -q
docker compose down --remove-orphans
```

[字段和时间窗](docs/SPEC.md) · [模块与回归表](docs/DEPTH.md) · [HTTP 与自备数据](docs/RUNBOOK.md)
