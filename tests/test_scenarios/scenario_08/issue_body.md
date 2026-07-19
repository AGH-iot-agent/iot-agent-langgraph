**Hello,**

The `iot-agent-authentication` pods are stuck in `CrashLoopBackOff` in the `iotag-dev` namespace. The application fails to start because it cannot connect to the PostgreSQL database — the datasource URL appears to point to `localhost` instead of the in-cluster service hostname.

**Pod logs:**
```
2026-05-08T07:14:03.441Z ERROR 1 --- [main] com.zaxxer.hikari.pool.HikariPool        : HikariPool-1 - Exception during pool initialization.

org.postgresql.util.PSQLException: Connection to localhost:5432 refused.
Check that the hostname and port are correct and that the postmaster is accepting TCP/IP connections.
    at org.postgresql.core.v3.ConnectionFactoryImpl.openConnectionImpl(ConnectionFactoryImpl.java:303)
    at org.postgresql.core.ConnectionFactory.openConnection(ConnectionFactory.java:51)
    at org.postgresql.jdbc.PgConnection.<init>(PgConnection.java:225)

2026-05-08T07:14:03.443Z ERROR 1 --- [main] o.s.b.SpringApplication                  : Application run failed

org.springframework.beans.factory.BeanCreationException: Error creating bean with name 'dataSource':
Failed to obtain JDBC Connection: HikariPool-1 - Connection is not available, request timed out after 30001ms.
```

**Pod status:**
```
NAME                                          READY   STATUS             RESTARTS   AGE
iot-agent-authentication-7d9f6b8c4-xkp2n      0/1     CrashLoopBackOff   5          8m
```

It looks like `SPRING_DATASOURCE_URL` in the Helm values is set to `jdbc:postgresql://localhost:5432/iotdb` instead of the correct Kubernetes service DNS `jdbc:postgresql://postgres.iotag-dev.svc.cluster.local:5432/iotdb`.

**To verify:**
```bash
kubectl get pod iot-agent-authentication-7d9f6b8c4-xkp2n -n iotag-dev -o jsonpath='{.spec.containers[0].env}' | jq
kubectl describe pod iot-agent-authentication-7d9f6b8c4-xkp2n -n iotag-dev
```

Please investigate the datasource configuration in `Helm/values-dev.yaml` and correct the `SPRING_DATASOURCE_URL` value.
