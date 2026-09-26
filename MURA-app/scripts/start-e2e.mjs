import { spawn } from "node:child_process";

const child = spawn(process.execPath, ["node_modules/next/dist/bin/next", "dev", "-p", "3100", "-H", "127.0.0.1"], {
  env: {
    ...process.env,
    NODE_ENV: "development",
    MURA_AUTH_PROVIDER: "dev",
    MURA_DEV_AUTH: "true",
    NEXT_TELEMETRY_DISABLED: "1",
  },
  stdio: "inherit",
});

child.on("exit", (code) => { process.exitCode = code ?? 1; });
