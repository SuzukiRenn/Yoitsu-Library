# 匿名版正式入口

已确认域名 `sharedbooklist.xyz`，备案号 `鲁ICP备2026066257号-1`。
身份版不随匿名版公开。

部署前使用固定提交，备份配置及数据库并完成恢复校验；保持数据目录和密钥不变。
`BOOKLIST_ICP_RECORD` 应设为上述真实号码，显式空值仍表示不展示。

默认部署只监听 `127.0.0.1:8443`。操作员在服务器
`/etc/shared-booklist/ssh-access/public-domain.json` 写入以下内容后，
现有 `deploy/ssh_access.py prepare/verify` 才会生成并检查公网入口：

```json
{"domain":"sharedbooklist.xyz","icp_record":"鲁ICP备2026066257号-1"}
```

标记为 root 所有、权限 0600。错误标记会拒绝部署，不猜测域名。
后续更新沿用该标记，避免恢复为私有入口。

- 保留 `127.0.0.1:8443` 供 SSH、监控及证书续期使用。
- 仅匿名版反代发布公网 TCP 80/443；HTTP 跳转 HTTPS，非预期 Host 拒绝。
- 应用 8000、数据库、审核服务及身份版 8444 不发布。
- DNS 根域名 `@` 的 A 记录指向服务器公网 IPv4；不配置未经验证的 AAAA。
- 云安全组仅增加网站 TCP 80/443，保留既有 SSH 来源限制。

验收须同时检查外部 DNS、可信 TLS、HTTP 跳转、静态资源、封面、备案链接、
匿名身份和既有业务数据，并确认身份版仍为私有。服务器内回环验证不等于公网验收。

如需回退入口，先备份标记和当前反代配置，再移走标记，针对当前固定提交重新
执行 `prepare`、Compose `up -d proxy` 和 `verify`，关闭云安全组 80/443。
不恢复旧数据库覆盖用户贡献。证书、配置备份和业务备份不得提交 Git。

## 身份版子域名入口

`account.sharedbooklist.xyz` 通过现有 HTTPS 边缘代理分流到身份版独立内部网络。
`/etc/shared-booklist-identity/public-domain.json` 只接受固定域名与 `challenge` 或 `enabled` 模式；未配置时保持原来的私有部署。
启用前需要 A 记录、独立受信任证书和身份版更新；不能仅开放 8444 或应用端口代替 HTTPS 域名入口。
身份版静态资源使用其独立目录的 `public-current` 指针。身份版更新成功后自动切换指针；账户 Cookie 保持主机作用域。
证书由 `yoitsu-identity-certificate.timer` 定期续期，成功后测试并重载边缘代理。
回退公网接入应删除身份版公网标记并用当前匿名版 `ssh_access.py prepare` 重新生成代理配置；不要回滚业务数据库。
