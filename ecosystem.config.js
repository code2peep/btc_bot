module.exports = {
  apps: [
    {
      name: "btc-crash-monitor",
      script: "./scripts/run_monitor.sh",
      cwd: __dirname,
      interpreter: "/bin/bash",
      exec_mode: "fork",
      autorestart: true,
      max_restarts: 20,
      restart_delay: 5000,
      kill_timeout: 10000,
      time: true,
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
  ],
};
