const STATES = new Set(["idle", "listening", "thinking", "answering", "happy"]);
const XP_ANIMATION_MS = 1600;
const FRAME_MS = 1000 / 30;

function roundRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.fill();
}

function drawEyes(ctx, state, blinking, sparkle) {
  ctx.clearRect(0, 0, 160, 80);
  ctx.fillStyle = state === "happy" ? "#ffdc8d" : "#c6ffe4";
  ctx.strokeStyle = ctx.fillStyle;
  ctx.lineWidth = 5;
  ctx.lineCap = "round";

  for (const x of [53, 107]) {
    if (!blinking && (state === "answering" || state === "happy")) {
      ctx.beginPath();
      ctx.moveTo(x - 9, 43);
      ctx.quadraticCurveTo(x, state === "happy" ? 22 : 27, x + 9, 43);
      ctx.stroke();
      continue;
    }
    const height = blinking ? 3 : state === "listening" ? 30 : state === "thinking" ? 12 : 25;
    const shift = state === "thinking" ? 5 : 0;
    const mid = state === "thinking" ? 35 : 39;
    roundRect(ctx, x - 7 + shift, mid - height / 2, 14, height, 7);
  }

  if (state === "thinking" && !blinking) {
    ctx.fillStyle = "#7cd5b5";
    for (const x of [73, 80, 87]) roundRect(ctx, x, 53, 3, 3, 1);
  }
  if (sparkle) {
    ctx.fillStyle = "#fff2ba";
    for (const [x, y, radius] of [[79, 24, 7], [31, 43, 4], [129, 33, 5]]) {
      ctx.beginPath();
      ctx.moveTo(x, y - radius);
      ctx.quadraticCurveTo(x + 1, y - 1, x + radius, y);
      ctx.quadraticCurveTo(x + 1, y + 1, x, y + radius);
      ctx.quadraticCurveTo(x - 1, y + 1, x - radius, y);
      ctx.quadraticCurveTo(x - 1, y - 1, x, y - radius);
      ctx.fill();
    }
  }
}

function releaseScene(root) {
  const geometries = new Set();
  const materials = new Set();
  const textures = new Set();
  root.traverse((object) => {
    if (object.geometry) geometries.add(object.geometry);
    if (object.material) {
      for (const material of [].concat(object.material)) {
        materials.add(material);
        for (const value of Object.values(material)) {
          if (value?.isTexture) textures.add(value);
        }
      }
    }
  });
  for (const texture of textures) texture.dispose();
  for (const material of materials) material.dispose();
  for (const geometry of geometries) geometry.dispose();
}

export function mountWomby(container) {
  const THREE = window.THREE;
  const motion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let requestedState = "idle";
  let rewardTimer = 0;
  let rewardStarted = 0;
  let celebrating = false;
  let disposed = false;
  let update = () => {};
  let stop = () => {};

  function publishState() {
    container.dataset.wombyState = celebrating ? "happy" : requestedState;
    update();
  }

  function endCelebration() {
    clearTimeout(rewardTimer);
    celebrating = false;
  }

  const controller = {
    setState(state) {
      if (disposed || !STATES.has(state)) return;
      if (state === "listening" || state === "thinking") endCelebration();
      requestedState = state;
      publishState();
    },
    celebrate(event) {
      if (disposed || !Number.isFinite(event?.gained) || event.gained <= 0) return;
      clearTimeout(rewardTimer);
      rewardStarted = performance.now();
      celebrating = true;
      publishState();
      rewardTimer = setTimeout(() => {
        celebrating = false;
        publishState();
      }, XP_ANIMATION_MS);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      endCelebration();
      stop();
      container.replaceChildren();
      delete container.dataset.wombyState;
      delete container.dataset.wombyModel;
    },
  };
  publishState();

  const message = document.createElement("p");
  message.setAttribute("role", "status");
  message.textContent = "Loading Womby…";
  container.replaceChildren(message);
  container.dataset.wombyModel = "loading";

  function unavailable() {
    if (disposed) return;
    stop();
    update = () => {};
    stop = () => {};
    container.dataset.wombyModel = "unavailable";
    message.textContent = "Womby’s 3D preview is unavailable here.";
    container.replaceChildren(message);
  }

  if (!THREE?.GLTFLoader) {
    unavailable();
    return controller;
  }

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
  } catch {
    unavailable();
    return controller;
  }

  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
  renderer.outputEncoding = THREE.sRGBEncoding;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.domElement.style.cssText = "display:block;width:100%;height:100%";
  renderer.domElement.setAttribute("aria-hidden", "true");

  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1.1, 1.1, 1.1, -1.1, 0.1, 20);
  camera.position.set(0, 0.08, 4);
  camera.lookAt(0, 0.02, 0);
  const figure = new THREE.Group();
  scene.add(figure, new THREE.HemisphereLight(0xffffff, 0xe5c5b6, 0.85));
  const light = new THREE.DirectionalLight(0xfff6e5, 0.8);
  light.position.set(-3, 4, 5);
  scene.add(light);

  // The source has one rigid mesh. Anchor a transparent eye layer in the measured black visor.
  const screen = document.createElement("canvas");
  screen.width = 160;
  screen.height = 80;
  const ctx = screen.getContext("2d");
  const eyeTexture = new THREE.CanvasTexture(screen);
  eyeTexture.encoding = THREE.sRGBEncoding;
  eyeTexture.minFilter = THREE.LinearFilter;
  eyeTexture.generateMipmaps = false;
  const eyes = new THREE.Mesh(
    new THREE.PlaneGeometry(0.75, 0.375),
    new THREE.MeshBasicMaterial({ map: eyeTexture, transparent: true, depthWrite: false, toneMapped: false }),
  );
  eyes.position.set(0, 0.342, 0.466);
  eyes.rotation.x = -0.052;
  figure.add(eyes);

  let frame = 0;
  let lastRender = -Infinity;
  let lastEyes = "";
  let visible = false;
  let ready = false;

  function canRender() {
    return !disposed && ready && visible && !document.hidden && container.clientWidth > 0 && container.clientHeight > 0;
  }

  function render(now) {
    const time = now / 1000;
    const state = container.dataset.wombyState;
    const reduced = motion.matches;
    const blinks = state === "idle" || state === "listening";
    const blink = !reduced && blinks && time % 4.7 > 4.51;
    const eyeKey = state + String(blink) + String(celebrating);
    if (eyeKey !== lastEyes) {
      drawEyes(ctx, state, blink, celebrating);
      eyeTexture.needsUpdate = true;
      lastEyes = eyeKey;
    }

    const lift = celebrating ? Math.max(0, 1 - (now - rewardStarted) / XP_ANIMATION_MS) : 0;
    figure.position.y = reduced ? 0 : Math.sin(time * 2) * 0.012 + lift * Math.abs(Math.sin(time * 11)) * 0.075;
    figure.rotation.z = reduced ? 0 : state === "thinking" ? -0.07 : Math.sin(time * 1.7) * 0.018;
    figure.rotation.y = reduced ? 0 : Math.sin(time * 1.4) * (state === "listening" ? 0.05 : 0.035);
    figure.rotation.x = !reduced && state === "answering" ? Math.sin(time * 4.4) * 0.02 : 0;
    renderer.render(scene, camera);
  }

  function loop(now) {
    frame = 0;
    if (!canRender() || motion.matches) return;
    if (now - lastRender >= FRAME_MS) {
      render(now);
      lastRender = now;
    }
    frame = requestAnimationFrame(loop);
  }

  function resize() {
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (!width || !height) return;
    const aspect = width / height;
    const halfHeight = Math.max(1.08, 0.89 / aspect);
    camera.top = halfHeight;
    camera.bottom = -halfHeight;
    camera.left = -halfHeight * aspect;
    camera.right = halfHeight * aspect;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height, false);
    update();
  }

  update = () => {
    if (!canRender()) return;
    render(performance.now());
    if (!motion.matches && !frame) frame = requestAnimationFrame(loop);
  };
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(container);
  const visibilityObserver = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    update();
  });
  visibilityObserver.observe(container);
  document.addEventListener("visibilitychange", update);
  motion.addEventListener("change", update);
  renderer.domElement.addEventListener("webglcontextlost", unavailable);

  stop = () => {
    ready = false;
    cancelAnimationFrame(frame);
    resizeObserver.disconnect();
    visibilityObserver.disconnect();
    document.removeEventListener("visibilitychange", update);
    motion.removeEventListener("change", update);
    renderer.domElement.removeEventListener("webglcontextlost", unavailable);
    releaseScene(scene);
    renderer.dispose();
  };

  const modelUrl = new URL("./womby.glb", import.meta.url).href;
  new THREE.GLTFLoader().load(modelUrl, ({ scene: model }) => {
    if (disposed || container.dataset.wombyModel === "unavailable") {
      releaseScene(model);
      return;
    }
    model.traverse((object) => {
      if (object.material?.normalScale) object.material.normalScale.multiplyScalar(0.35);
    });
    figure.add(model);
    container.replaceChildren(renderer.domElement);
    container.dataset.wombyModel = "ready";
    ready = true;
    resize();
  }, undefined, unavailable);
  return controller;
}
