# 运行维护

主服务端口与Compose保持一致。数据保存在records命名卷，应用代码不需要写入权限。

```sh
docker compose logs --tail=80 app
docker compose restart app
docker compose exec -T app pytest -q
docker compose down --remove-orphans
```

健康检查GET /health。失败输入返回原因码，结构性错误422，过大文件413，陈旧复核409。
文件内容最大4MB。批次先计算再原子保存，失败不留半条记录。证据CSV按原始行号定位。

封床版本保存在同库 `hold_versions` 与 `hold_active` 两表：候选版本先落库再原子切换，切换失败保留 `切换失败` 记录并沿用旧生效容量；重试相同父版本与裁决复用原版本号。导出 `/api/holds.csv` 同时并列批次封床、生效封床与容量日历。
