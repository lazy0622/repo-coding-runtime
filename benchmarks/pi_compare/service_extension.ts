/** Explicit evaluation extension; no project or ambient extension discovery. */
import { createBashTool, createLocalBashOperations } from "@earendil-works/pi-coding-agent";

const allowed = new Set([
  "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "USERPROFILE",
  "HOME", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE",
]);

function shellEnv(source: NodeJS.ProcessEnv) {
  const env = Object.fromEntries(
    Object.entries(source).filter(([key]) => allowed.has(key.toUpperCase())),
  );
  return { ...env, PYTHONUTF8: "1", PYTHONIOENCODING: "utf-8" };
}

export default function (pi) {
  const bash = createBashTool(process.cwd(), {
    shellPath: process.env.RUNTIME_PI_BASH,
    exposeSessionEnvironment: false,
    spawnHook: ({ command, cwd, env }) => ({ command, cwd, env: shellEnv(env) }),
  });
  pi.registerTool({
    ...bash,
    execute: (id, params, signal, onUpdate) => bash.execute(id, params, signal, onUpdate),
  });
  const local = createLocalBashOperations({ shellPath: process.env.RUNTIME_PI_BASH });
  pi.on("user_bash", () => ({
    operations: {
      exec: (command, cwd, options) => local.exec(command, cwd, {
        ...options, env: shellEnv(options.env ?? process.env),
      }),
    },
  }));
  pi.on("before_provider_request", (event, ctx) => {
    if (ctx.model?.provider === "runtime-deepseek") {
      return { ...event.payload, thinking: { type: "disabled" }, max_tokens: 4096 };
    }
  });
}
