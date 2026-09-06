// Waybar PiP — bascule en Picture-in-Picture la vidéo que Chrome joue, même
// quand son onglet n'est pas celui qu'on regarde.
//
// ── Pourquoi cette extension existe ──────────────────────────────────────────
// Le clic droit du module `mpris` appelle `mpris-pip.sh`, qui focalise une
// fenêtre Chrome puis envoie Alt+P. Ce raccourci est le seul canal disponible :
// rien, depuis l'extérieur, ne permet de désigner un onglet. L'extension
// officielle de Google agit alors sur l'onglet ACTIF de cette fenêtre — donc
// sur rien du tout dès que la vidéo est passée en arrière-plan, et sans le
// moindre message. Celle-ci garde le même déclencheur (Alt+P câblé sur
// `_execute_action`, le seul chemin qui propage l'activation utilisateur
// jusqu'à la page — une commande nommée ne l'a pas toujours) mais choisit
// l'onglet elle-même.
//
// ── Ordre de recherche, du moins intrusif au plus ────────────────────────────
//   1. l'onglet actif de la fenêtre au premier plan : cas courant, et personne
//      ne voit rien bouger ;
//   2. sinon l'onglet qui porte une vidéo — celle qui joue d'abord, la plus
//      grande ensuite — et le PiP est tenté sans quitter l'onglet courant ;
//   3. si la page refuse (l'activation utilisateur ne survit pas toujours à un
//      onglet caché), on bascule dessus le temps du PiP et on revient aussitôt.
//
// Le PiP est une bascule : si une vidéo est déjà sortie, on la remet dedans.
//
// La sélection de vidéo dans la page reprend l'heuristique de l'extension de
// Google (readyState, disablePictureInPicture, tri par surface), à ceci près
// qu'une vidéo qui JOUE l'emporte sur une plus grande à l'arrêt : sur un fil
// d'actualité, l'aperçu au survol est souvent le plus gros <video> de la page.

// Au-delà, on sonde trop d'onglets pour tenir dans le délai d'activation
// utilisateur (~5 s) — et personne n'a une vidéo dans son 13e onglet le plus
// récemment consulté.
const PROBE_LIMIT = 12;

// Laisse à Chrome le temps de rendre l'onglet visible : sous ce seuil, la page
// est encore `hidden` et refuse le PiP pour la raison même qu'on essaie de
// corriger.
const SWITCH_DELAY = 150;

chrome.action.onClicked.addListener((tab) => {
  run(tab).catch((err) => notify("Échec", String(err && err.message || err)));
});

async function run(activeTab) {
  // 1. l'onglet sous les yeux.
  if (isWeb(activeTab)) {
    const here = await pip(activeTab.id);
    if (here.ok) return;
    // Il avait bien une vidéo, mais la page a dit non : on s'arrête là. Aller
    // en sortir une autre serait la pire des réponses — l'utilisateur regarde
    // celle-ci.
    if (here.reason !== "no-video") {
      notify("Picture-in-Picture refusé", here.reason);
      return;
    }
  }

  // 2. l'onglet qui a une vidéo, ailleurs.
  const target = await findVideoTab(activeTab && activeTab.id);
  if (!target) {
    notify("Aucune vidéo", "Aucun onglet Chrome ne porte de vidéo sortable.");
    return;
  }

  let out = await pip(target.id);

  // 3. dernier recours : on va la chercher.
  if (!out.ok) out = await pipViaSwitch(target);

  if (!out.ok) notify("Picture-in-Picture refusé", out.reason || "raison inconnue");
}

// Sans la permission `tabs`, l'URL n'est exposée que pour les onglets couverts
// par `host_permissions` — les pages internes de Chrome n'en ont donc pas, et
// ce filtre les écarte en même temps que celles où l'injection est interdite.
function isWeb(tab) {
  return !!(tab && tab.url && /^https?:/.test(tab.url));
}

// ── Injection ────────────────────────────────────────────────────────────────
// `allFrames` est indispensable : sur bien des sites la vidéo vit dans une
// iframe (lecteur YouTube embarqué, Twitch, players de presse). Chaque frame
// répond, on retient la première qui a abouti — et à défaut le premier échec
// PARLANT, pour ne pas rapporter « pas de vidéo » alors qu'une frame a
// vraiment essayé.

async function pip(tabId) {
  let frames;
  try {
    frames = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func: pipInPage,
    });
  } catch (err) {
    return { ok: false, reason: err.message };
  }
  const results = frames.map((f) => f.result).filter(Boolean);
  return results.find((r) => r.ok)
      || results.find((r) => r.reason !== "no-video")
      || { ok: false, reason: "no-video" };
}

function pipInPage() {
  const videos = Array.from(document.querySelectorAll("video"))
    .filter((v) => v.readyState !== 0 && !v.disablePictureInPicture);
  if (!videos.length) return { ok: false, reason: "no-video" };

  const area = (v) => {
    const r = v.getClientRects()[0];
    return r ? r.width * r.height : 0;
  };
  videos.sort((a, b) => (a.paused === b.paused ? area(b) - area(a) : (a.paused ? 1 : -1)));

  if (document.pictureInPictureElement) {
    document.exitPictureInPicture();
    return { ok: true, exited: true };
  }
  return videos[0].requestPictureInPicture()
    .then(() => ({ ok: true }))
    .catch((e) => ({ ok: false, reason: e.name + " : " + e.message }));
}

// ── Recherche de l'onglet ────────────────────────────────────────────────────

async function findVideoTab(excludeId) {
  const tabs = (await chrome.tabs.query({}))
    .filter((t) => t.id !== excludeId && isWeb(t) && !t.discarded)
    // Un onglet qui fait du bruit passe devant ; à égalité, le plus récemment
    // consulté. Sur un profil chargé, c'est ce tri qui garde la sonde courte.
    .sort((a, b) => (b.audible - a.audible) || ((b.lastAccessed || 0) - (a.lastAccessed || 0)))
    .slice(0, PROBE_LIMIT);

  const probes = await Promise.all(tabs.map((t) => probe(t.id)));

  let best = null;
  tabs.forEach((tab, i) => {
    const p = probes[i];
    if (!p) return;
    if (!best || rank(p) > rank(best.p)) best = { tab, p };
  });
  return best ? best.tab : null;
}

// Un onglet qui a DÉJÀ un PiP l'emporte : Alt+P est une bascule, et le refermer
// est alors le geste attendu. Vient ensuite ce qui joue, puis la plus grande.
function rank(p) {
  return (p.pip ? 4e12 : 0) + (p.playing ? 2e12 : 0) + Math.min(p.area, 1e12);
}

async function probe(tabId) {
  let frames;
  try {
    frames = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      func: probeInPage,
    });
  } catch {
    return null;   // onglet non injectable (Web Store, PDF, page d'erreur)
  }
  const results = frames.map((f) => f.result).filter(Boolean);
  if (!results.length) return null;
  return {
    pip: results.some((r) => r.pip),
    playing: results.some((r) => r.playing),
    area: Math.max(...results.map((r) => r.area)),
  };
}

function probeInPage() {
  const videos = Array.from(document.querySelectorAll("video"))
    .filter((v) => v.readyState !== 0 && !v.disablePictureInPicture);
  if (!videos.length) return null;
  const area = (v) => {
    const r = v.getClientRects()[0];
    return r ? r.width * r.height : 0;
  };
  return {
    pip: !!document.pictureInPictureElement,
    playing: videos.some((v) => !v.paused),
    area: Math.max(...videos.map(area)),
  };
}

async function pipViaSwitch(tab) {
  const [previous] = await chrome.tabs.query({ active: true, windowId: tab.windowId });
  await chrome.tabs.update(tab.id, { active: true });
  await new Promise((r) => setTimeout(r, SWITCH_DELAY));

  const out = await pip(tab.id);

  // La fenêtre PiP est flottante : elle survit au retour sur l'onglet d'avant.
  if (previous && previous.id !== tab.id) {
    await chrome.tabs.update(previous.id, { active: true });
  }
  return out;
}

// Une notification plutôt qu'un échec muet : c'est le silence de l'extension de
// Google qui a fait passer ce clic droit pour cassé pendant des semaines.
function notify(title, message) {
  chrome.notifications.create({
    type: "basic",
    iconUrl: "assets/icon128.png",
    title: "Waybar PiP — " + title,
    message,
  });
}
