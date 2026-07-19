**Hello,**

The CI pipeline for `iot-agent-device-api` is failing at the Maven build step with a dependency resolution error.

Full error from the GitHub Actions job log:

```
[INFO] Scanning for projects...
[INFO] ------------------------------------------------------------------------
[INFO] BUILD FAILURE
[INFO] ------------------------------------------------------------------------
[ERROR] Failed to execute goal on project iot-agent-device-api:
Could not resolve dependencies for project com.iotag:iot-agent-device-api:jar:0.1.0-SNAPSHOT:
Could not find artifact com.iotag:iot-agent-common:jar:2.1.0 in central (https://repo1.maven.org/maven2):
artifact not found
[ERROR] -> [Help 1]
```

The latest commit to `main` bumped the dependency on `iot-agent-common` from version `2.0.1` to `2.1.0` in `pom.xml`, but this version has not yet been published to the GitHub Packages registry.

**Relevant `pom.xml` diff:**
```xml
-  <version>2.0.1</version>
+  <version>2.1.0</version>
```

Please investigate and either:
- Revert the version bump to `2.0.1` in `pom.xml`, or
- Publish the `iot-agent-common:2.1.0` artifact to the package registry before this pipeline can succeed.
