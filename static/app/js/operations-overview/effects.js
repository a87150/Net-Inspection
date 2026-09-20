const NOOP_CONTROLLER = Object.freeze({ destroy() {} });

export function createAmbientEffects(canvas, {
  documentRef = globalThis.document,
  windowRef = globalThis.window,
} = {}) {
  if (!canvas || !documentRef || !windowRef) return NOOP_CONTROLLER;
  if (windowRef.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return NOOP_CONTROLLER;
  const context = canvas.getContext?.('2d');
  if (!context) return NOOP_CONTROLLER;

  let frameId = null;
  let destroyed = false;
  let particles = [];
  let width = 0;
  let height = 0;

  function resize() {
    const bounds = canvas.getBoundingClientRect();
    const ratio = Math.min(2, Math.max(1, windowRef.devicePixelRatio || 1));
    width = Math.max(1, bounds.width);
    height = Math.max(1, bounds.height);
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    const count = width < 768 ? 18 : Math.min(48, Math.round(width / 28));
    particles = Array.from({ length: count }, (_, index) => ({
      x: (index * 83) % width,
      y: (index * 47) % height,
      radius: 0.7 + ((index % 4) * 0.25),
      velocity: 0.08 + ((index % 5) * 0.025),
    }));
  }

  function draw() {
    frameId = null;
    if (destroyed || documentRef.hidden) return;
    context.clearRect(0, 0, width, height);
    context.fillStyle = 'rgba(75, 224, 255, 0.34)';
    for (const particle of particles) {
      particle.y -= particle.velocity;
      if (particle.y < -4) particle.y = height + 4;
      context.beginPath();
      context.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2);
      context.fill();
    }
    frameId = windowRef.requestAnimationFrame(draw);
  }

  function resume() {
    if (!destroyed && !documentRef.hidden && frameId === null) {
      frameId = windowRef.requestAnimationFrame(draw);
    }
  }

  function handleVisibility() {
    if (documentRef.hidden && frameId !== null) {
      windowRef.cancelAnimationFrame(frameId);
      frameId = null;
      return;
    }
    resume();
  }

  resize();
  resume();
  windowRef.addEventListener('resize', resize, { passive: true });
  documentRef.addEventListener('visibilitychange', handleVisibility);

  return {
    destroy() {
      destroyed = true;
      if (frameId !== null) windowRef.cancelAnimationFrame(frameId);
      frameId = null;
      particles = [];
      windowRef.removeEventListener('resize', resize);
      documentRef.removeEventListener('visibilitychange', handleVisibility);
      context.clearRect(0, 0, width, height);
    },
  };
}
