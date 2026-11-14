/**
 * body/rendering/webgl_viewport/viewport.js
 * Milestone B1: Three.js WebGL 2 Diagnostic Scene & Local IPC Client
 */

(function () {
  // 1. Parse URL query params
  const params = new URLSearchParams(window.location.search);
  const wsPort = params.get("ws") || "9876";
  const token = params.get("token") || "";

  // 2. Setup Three.js WebGL 2 scene
  const canvas = document.getElementById("viewport-canvas");
  const renderer = new THREE.WebGLRenderer({
    canvas: canvas,
    antialias: true,
    alpha: true,
    powerPreference: "high-performance",
  });
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2.0));
  renderer.outputEncoding = THREE.sRGBEncoding;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, window.innerWidth / window.innerHeight, 0.1, 100);
  camera.position.set(0, 1.35, 2.6);
  camera.lookAt(0, 1.2, 0);

  // 3. Lighting Setup
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.65);
  scene.add(ambientLight);

  const keyLight = new THREE.DirectionalLight(0xfff5ea, 1.2);
  keyLight.position.set(1.5, 3.0, 2.0);
  scene.add(keyLight);

  const rimLight = new THREE.DirectionalLight(0x7055ff, 1.5); // Blue-violet rim light matching JARVIS eyes
  rimLight.position.set(-2.0, 2.0, -2.0);
  scene.add(rimLight);

  const fillLight = new THREE.DirectionalLight(0x00d4ff, 0.4); // Subtle cyan fill
  fillLight.position.set(-1.5, 1.0, 1.5);
  scene.add(fillLight);

  // 4. Construct Diagnostic 3D Test Geometry (Stylized Humanoid Reference Bust)
  const avatarGroup = new THREE.Group();
  avatarGroup.position.set(0, 0.5, 0);

  // Torso / High-neck collar base
  const torsoGeo = new THREE.CylinderGeometry(0.32, 0.38, 0.7, 32);
  const outfitMat = new THREE.MeshStandardMaterial({
    color: 0x111418,
    roughness: 0.7,
    metalness: 0.1,
  });
  const torso = new THREE.Mesh(torsoGeo, outfitMat);
  torso.position.y = 0.35;
  avatarGroup.add(torso);

  // High neck collar
  const neckGeo = new THREE.CylinderGeometry(0.14, 0.16, 0.28, 32);
  const neck = new THREE.Mesh(neckGeo, outfitMat);
  neck.position.y = 0.8;
  avatarGroup.add(neck);

  // Head
  const headGeo = new THREE.SphereGeometry(0.24, 32, 32);
  headGeo.scale(1.0, 1.25, 1.1);
  const skinMat = new THREE.MeshStandardMaterial({
    color: 0xf5e2d5,
    roughness: 0.45,
    metalness: 0.05,
  });
  const head = new THREE.Mesh(headGeo, skinMat);
  head.position.y = 1.15;
  avatarGroup.add(head);

  // Hair Placeholder Volume (Silver-white flowing locks)
  const hairGeo = new THREE.SphereGeometry(0.27, 32, 32);
  hairGeo.scale(1.05, 1.35, 1.2);
  const hairMat = new THREE.MeshStandardMaterial({
    color: 0xebf2f8,
    roughness: 0.3,
    metalness: 0.25,
  });
  const hair = new THREE.Mesh(hairGeo, hairMat);
  hair.position.set(0, 1.25, -0.05);
  avatarGroup.add(hair);

  // Left & Right Blue-Violet Eyes
  const eyeGeo = new THREE.SphereGeometry(0.04, 16, 16);
  const eyeMat = new THREE.MeshStandardMaterial({
    color: 0x4d38ff,
    emissive: 0x2211aa,
    roughness: 0.1,
  });
  const leftEye = new THREE.Mesh(eyeGeo, eyeMat);
  leftEye.position.set(-0.085, 1.18, 0.22);
  const rightEye = new THREE.Mesh(eyeGeo, eyeMat);
  rightEye.position.set(0.085, 1.18, 0.22);
  avatarGroup.add(leftEye);
  avatarGroup.add(rightEye);

  scene.add(avatarGroup);

  // 5. Telemetry & Frame-Time Distribution Benchmarker
  const frameDeltas = [];
  const MAX_DELTA_SAMPLES = 120;
  let lastFrameTime = performance.now();
  let totalFrames = 0;
  let lastReportTime = 0;

  const hudFps = document.getElementById("hud-fps");
  const hudFt = document.getElementById("hud-ft");
  const hudP95 = document.getElementById("hud-p95");
  const hudState = document.getElementById("hud-state");
  const ipcStatus = document.getElementById("ipc-status");

  let currentVisualState = "IDLE";

  // 6. WebSocket IPC Connection
  let ws = null;

  function initWebSocket() {
    const wsUrl = `ws://127.0.0.1:${wsPort}?token=${encodeURIComponent(token)}`;
    ws = new WebSocket(wsUrl);

    ws.onopen = function () {
      ipcStatus.textContent = "CONNECTED (Authenticated)";
      ipcStatus.className = "status-ok";
      // Send initial auth packet
      ws.send(JSON.stringify({ type: "auth", token: token }));
    };

    ws.onmessage = function (event) {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "ping") {
          // Microsecond pong response for RTT latency measurement
          ws.send(JSON.stringify({
            type: "pong",
            client_ts: msg.client_ts,
            server_ts: performance.now(),
          }));
        } else if (msg.type === "visual_state") {
          currentVisualState = msg.state || "IDLE";
          hudState.textContent = currentVisualState;

          // React visually to states without blocking
          if (currentVisualState === "THINKING") {
            rimLight.color.setHex(0xffaa22); // Amber accent
          } else if (currentVisualState === "SPEAKING") {
            rimLight.color.setHex(0x00d4ff); // Bright cyan accent
          } else {
            rimLight.color.setHex(0x7055ff); // Default violet
          }
          ws.send(JSON.stringify({ type: "state_ack", state: currentVisualState }));
        }
      } catch (err) {
        console.error("IPC Parse Error:", err);
      }
    };

    ws.onclose = function () {
      ipcStatus.textContent = "DISCONNECTED (Retrying...)";
      ipcStatus.className = "status-warn";
      setTimeout(initWebSocket, 1000);
    };

    ws.onerror = function () {
      ipcStatus.textContent = "ERROR";
      ipcStatus.className = "status-warn";
    };
  }

  initWebSocket();

  // 7. Render Loop with High-Precision Telemetry
  function animate(now) {
    requestAnimationFrame(animate);

    const delta = now - lastFrameTime;
    lastFrameTime = now;
    totalFrames++;

    // Track frame delta in sliding window
    frameDeltas.push(delta);
    if (frameDeltas.length > MAX_DELTA_SAMPLES) {
      frameDeltas.shift();
    }

    // Procedural Organic Breathing Motion (harmonic sine wave)
    const breathOffset = Math.sin(now * 0.002) * 0.015;
    torso.scale.set(1.0 + breathOffset * 0.5, 1.0 + breathOffset, 1.0 + breathOffset * 0.5);
    head.position.y = 1.15 + breathOffset * 0.8;
    hair.position.y = 1.25 + breathOffset * 0.8;

    // Subtle gentle head rotation
    avatarGroup.rotation.y = Math.sin(now * 0.0008) * 0.12;

    renderer.render(scene, camera);

    // Periodic Telemetry calculation and reporting (every 250ms)
    if (now - lastReportTime >= 250 && frameDeltas.length >= 10) {
      lastReportTime = now;

      const sorted = [...frameDeltas].sort((a, b) => a - b);
      const avgDelta = sorted.reduce((sum, v) => sum + v, 0) / sorted.length;
      const p50 = sorted[Math.floor(sorted.length * 0.50)];
      const p95 = sorted[Math.floor(sorted.length * 0.95)];
      const fps = 1000.0 / avgDelta;

      hudFps.textContent = fps.toFixed(1);
      hudFt.textContent = avgDelta.toFixed(1) + " ms";
      hudP95.textContent = p95.toFixed(1) + " ms";

      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: "telemetry_report",
          fps: parseFloat(fps.toFixed(2)),
          frame_time_ms: parseFloat(avgDelta.toFixed(2)),
          p50_ms: parseFloat(p50.toFixed(2)),
          p95_ms: parseFloat(p95.toFixed(2)),
          frame_count: totalFrames,
        }));
      }
    }
  }

  // Handle Window Resizing
  window.addEventListener("resize", function () {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
  });

  requestAnimationFrame(animate);
})();
