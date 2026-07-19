**Hello,**

I'm getting an error when trying to build `iot-agent-authentication` from my feature branch.

Branch: `test/secret-missmatch-test`

The error:

```
Current deployment image: iot-agent-authentication:300015c5126152d4e41a8e791563b796002ee9db
Target deployment image: iot-agent-authentication:33d81412e88fd4854158f84105512a504389219f
level=WARN msg="upgrade failed" name=iot-agent-authentication error="resource Deployment/iotag-sbx/iot-agent-authentication not ready. status: InProgress, message: Pending termination: 1\ncontext deadline exceeded"
Error: UPGRADE FAILED: resource Deployment/iotag-sbx/iot-agent-authentication not ready. status: InProgress, message: Pending termination: 1
context deadline exceeded
```

**Quick overview:**

I was trying to migrate secrets from inline Helm env values:

```
  - name: SPRING_DATASOURCE_URL
    value: "jdbc:postgresql://iot-agent-postgres-postgresql:5432/iot_agent"
  - name: SPRING_DATASOURCE_USERNAME
    value: "gte"
  - name: SPRING_DATASOURCE_PASSWORD
    value: "gte"
  - name: JWT_SECRET
    value: "supersecretkey"
```

to proper secret management via `secretKeyRef`:

```
extraEnvs:
  - name: SPRING_DATASOURCE_URL
    value: "jdbc:postgresql://iot-agent-postgres-postgresql:5432/iot_agent"
  - name: SPRING_DATASOURCE_USERNAME
    valueFrom:
      secretKeyRef:
        name: sql-connection-secret
        key: username
  - name: SPRING_DATASOURCE_PASSWORD
    valueFrom:
      secretKeyRef:
        name: sql-connection-secret
        key: password
  - name: JWT_SECRET
    value: "supersecretkey"
```

Is there anything I might be missing?