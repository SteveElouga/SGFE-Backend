const { Client, RemoteAuth } = require('whatsapp-web.js');
const crypto = require('crypto');
const express = require('express');
const Redis = require('ioredis');
const qrcodeTerminal = require('qrcode-terminal');
const QRCode = require('qrcode');
const { RedisStore } = require('./redis-store');

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 3000;
const SESSION_PATH = process.env.SESSION_PATH || '/app/session';
const INTERNAL_API_KEY = process.env.WHATSAPP_INTERNAL_API_KEY || '';
const REDIS_URL = process.env.REDIS_URL || 'redis://redis:6379/0';
// Intervalle de sauvegarde périodique de la session vers Redis (minimum 60000 ms
// imposé par RemoteAuth). La toute première sauvegarde a lieu 60 s après le scan
// du QR ; au-delà, la session persistée survit à tout redémarrage du container.
const BACKUP_INTERVAL_MS = Number(process.env.WHATSAPP_BACKUP_INTERVAL_MS) || 300000;

// Fail-closed : sans clé d'authentification interne, /qr, /send et /send-with-pdf
// seraient exposés sans protection. On refuse donc de démarrer le service (ANO-005).
if (!INTERNAL_API_KEY) {
    console.error(
        '[WhatsApp] FATAL : WHATSAPP_INTERNAL_API_KEY absente ou vide. Le service refuse ' +
        "de démarrer sans clé d'authentification interne. Définissez-la dans l'environnement " +
        '(y compris en local) avant de lancer le service.'
    );
    process.exit(1);
}

/**
 * Protège les endpoints sensibles (QR code, envoi de messages) avec une clé
 * partagée entre ce service et ses appelants (auth-service, notification-service).
 * Comparaison en temps constant pour éviter les attaques par timing.
 */
function requireApiKey(req, res, next) {
    // La clé est garantie présente (fail-closed au démarrage) — on la valide toujours.
    const provided = Buffer.from(req.get('X-Internal-Api-Key') || '');
    const expected = Buffer.from(INTERNAL_API_KEY);
    const valid = provided.length === expected.length && crypto.timingSafeEqual(provided, expected);

    if (!valid) {
        return res.status(401).json({ success: false, error: 'Clé API interne invalide ou manquante' });
    }
    next();
}

let isReady = false;
let currentQr = null;
let activeClient = null;
let restartCount = 0;

// Client Redis partagé : coffre-fort de la session WhatsApp (RemoteAuth y stocke
// un zip du profil, restauré au démarrage). C'est ce qui permet de survivre à un
// redémarrage/rebuild du container sans re-scanner le QR code.
const redis = new Redis(REDIS_URL, {
    // La session doit pouvoir être restaurée même si Redis vient lui-même de
    // redémarrer : on laisse ioredis retenter indéfiniment plutôt qu'échouer.
    maxRetriesPerRequest: null,
});
redis.on('error', (err) => console.error('[WhatsApp] Erreur Redis :', err.message));

const sessionStore = new RedisStore({ redis, dataPath: SESSION_PATH });

// Canal pub/sub sur lequel on pousse tout changement d'état (nouveau QR,
// connecté, déconnecté). La Gateway y est abonnée et relaie en temps réel à
// l'UI admin via une subscription GraphQL (plus besoin de poller le QR).
const WHATSAPP_EVENTS_CHANNEL = 'whatsapp:events';

/**
 * Publie l'état courant de la connexion WhatsApp sur Redis.
 * @param {{ ready: boolean, qr: string, number: string }} payload
 *   `qr` est une data-URL PNG prête pour un <img src> (vide si connecté).
 */
async function publishStatus(payload) {
    try {
        await redis.publish(WHATSAPP_EVENTS_CHANNEL, JSON.stringify(payload));
    } catch (err) {
        // Best-effort : un échec de publication ne doit jamais casser le cycle de
        // vie du client WhatsApp (l'UI retombe sur la query whatsappQr en repli).
        console.warn('[WhatsApp] Publication du statut sur Redis échouée :', err.message);
    }
}

function startClient() {
    // Pas de nettoyage de lock Chromium ici : RemoteAuth supprime et ré-extrait
    // un profil propre depuis Redis à chaque initialisation (extractRemoteSession),
    // donc aucun SingletonLock d'un arrêt brutal ne subsiste.
    const client = new Client({
        authStrategy: new RemoteAuth({
            store: sessionStore,
            dataPath: SESSION_PATH,
            backupSyncIntervalMs: BACKUP_INTERVAL_MS,
        }),
        puppeteer: {
            headless: true,
            args: [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
                '--no-zygote',
                '--disable-extensions',
            ],
        },
    });

    activeClient = client;

    client.on('qr', async (qr) => {
        currentQr = qr;
        qrcodeTerminal.generate(qr, { small: true });
        console.log(`[WhatsApp] QR code prêt — scannez-le sur http://localhost:${PORT}/qr`);
        // Pousse le nouveau QR (data-URL PNG, même format que /qr-data) en temps réel.
        const qrImage = await QRCode.toDataURL(qr);
        await publishStatus({ ready: false, qr: qrImage, number: '' });
    });

    client.on('authenticated', () => {
        console.log('[WhatsApp] Session authentifiée');
    });

    // Émis après chaque sauvegarde de la session vers Redis. Tant que ce message
    // n'est pas apparu au moins une fois (≈ 60 s après le tout premier scan),
    // un redémarrage exigerait un nouveau scan.
    client.on('remote_session_saved', () => {
        console.log('[WhatsApp] Session sauvegardée dans Redis — un redémarrage du container ne nécessitera plus de re-scan');
    });

    client.on('ready', async () => {
        isReady = true;
        restartCount = 0;
        currentQr = null;
        console.log('[WhatsApp] Client prêt — envoi de messages activé');
        const number = client.info?.wid?.user || '';
        await publishStatus({ ready: true, qr: '', number });
    });

    client.on('auth_failure', async (msg) => {
        console.error('[WhatsApp] Échec d\'authentification :', msg);
        isReady = false;
        await publishStatus({ ready: false, qr: '', number: '' });
        scheduleRestart(client);
    });

    client.on('disconnected', async (reason) => {
        if (activeClient !== client) return; // un nouveau cycle a déjà commencé
        console.warn('[WhatsApp] Déconnecté :', reason);
        isReady = false;
        await publishStatus({ ready: false, qr: '', number: '' });
        scheduleRestart(client);
    });

    client.initialize().catch((err) => {
        if (activeClient !== client) return;
        console.error('[WhatsApp] Erreur d\'initialisation :', err.message);
        isReady = false;
        scheduleRestart(client);
    });
}

async function scheduleRestart(clientRef) {
    // Détruire proprement le client actuel avant d'en créer un nouveau.
    // Un client whatsapp-web.js n'est pas réutilisable après disconnected/failure.
    try { await clientRef.destroy(); } catch (_) {}

    restartCount++;
    // Backoff exponentiel plafonné à 60 s pour ne pas saturer les logs
    const delay = Math.min(5000 * restartCount, 60_000);
    console.log(`[WhatsApp] Reconnexion dans ${delay / 1000} s (tentative n°${restartCount})`);
    setTimeout(startClient, delay);
}

// ── Endpoints ─────────────────────────────────────────────────────────────────

app.get('/health', (_req, res) => {
    // 503 tant que WhatsApp n'est pas connecté (init, QR à scanner, déconnexion) ;
    // 200 seulement quand le client peut réellement envoyer. Un orchestrateur ne doit
    // pas considérer le service « sain » s'il est incapable d'envoyer un message.
    res.status(isReady ? 200 : 503).json({ ready: isReady });
});

app.get('/qr', requireApiKey, async (_req, res) => {
    if (isReady) {
        return res.send('<p style="font-family:sans-serif;color:green">✓ WhatsApp connecté</p>');
    }
    if (!currentQr) {
        return res.send('<p style="font-family:sans-serif">Initialisation en cours, rechargez dans quelques secondes…</p>');
    }
    const qrImage = await QRCode.toDataURL(currentQr);
    res.send(`
        <!DOCTYPE html>
        <html>
        <head><title>SGFE — Connexion WhatsApp</title></head>
        <body style="font-family:sans-serif;text-align:center;padding:40px">
            <h2>Scannez ce QR code avec WhatsApp</h2>
            <p>WhatsApp → Appareils connectés → Connecter un appareil</p>
            <img src="${qrImage}" style="width:300px;height:300px" />
            <p><small>La page se rechargera automatiquement</small></p>
            <script>setTimeout(() => location.reload(), 5000)</script>
        </body>
        </html>
    `);
});

// Version machine du QR (JSON) destinée à être relayée par notification-service
// vers la Gateway, pour affichage dans l'UI admin. `qr` est une data-URL PNG
// prête à mettre dans un <img src>, vide si déjà connecté ou en cours d'init.
app.get('/qr-data', requireApiKey, async (_req, res) => {
    if (isReady) {
        // Numéro du compte WhatsApp appairé, pour affichage « N° du compte dédié ».
        let number = '';
        if (activeClient && activeClient.info && activeClient.info.wid) {
            number = activeClient.info.wid.user || '';
        }
        return res.json({ ready: true, qr: '', number });
    }
    if (!currentQr) {
        return res.json({ ready: false, qr: '', number: '' });
    }
    const qrImage = await QRCode.toDataURL(currentQr);
    res.json({ ready: false, qr: qrImage, number: '' });
});

app.post('/send', requireApiKey, async (req, res) => {
    if (!isReady || !activeClient) {
        return res.status(503).json({
            success: false,
            error: 'WhatsApp non connecté — scannez le QR code sur /qr',
        });
    }

    const { phone, message } = req.body;

    if (!phone || !message) {
        return res.status(400).json({ success: false, error: 'phone et message requis' });
    }

    try {
        const chatId = phone.replace('+', '') + '@c.us';
        await activeClient.sendMessage(chatId, message);
        console.log(`[WhatsApp] Message envoyé à ${phone}`);
        res.json({ success: true });
    } catch (err) {
        console.error(`[WhatsApp] Erreur envoi vers ${phone} :`, err.message);
        res.status(500).json({ success: false, error: err.message });
    }
});

app.post('/send-with-pdf', requireApiKey, async (req, res) => {
    if (!isReady || !activeClient) {
        return res.status(503).json({
            success: false,
            error: 'WhatsApp non connecté — scannez le QR code sur /qr',
        });
    }

    const { phone, message, pdf_base64, filename } = req.body;

    if (!phone || !pdf_base64) {
        return res.status(400).json({ success: false, error: 'phone et pdf_base64 requis' });
    }

    try {
        const { MessageMedia } = require('whatsapp-web.js');
        const media = new MessageMedia('application/pdf', pdf_base64, filename || 'facture.pdf');
        const chatId = phone.replace('+', '') + '@c.us';
        await activeClient.sendMessage(chatId, media, { caption: message || '' });
        console.log(`[WhatsApp] PDF envoyé à ${phone} (${filename || 'facture.pdf'})`);
        res.json({ success: true });
    } catch (err) {
        console.error(`[WhatsApp] Erreur envoi PDF vers ${phone} :`, err.message);
        res.status(500).json({ success: false, error: err.message });
    }
});

// ── Démarrage ─────────────────────────────────────────────────────────────────

app.listen(PORT, () => {
    console.log(`[WhatsApp] Service démarré sur le port ${PORT}`);
    console.log(`[WhatsApp] QR code disponible sur http://localhost:${PORT}/qr`);
});

startClient();

// ── Arrêt gracieux ──────────────────────────────────────────────────────────
// Sans ce handler, `docker stop` termine Node sans prévenir Chromium : Puppeteer
// tue alors le navigateur par SIGKILL, laissant le profil dans un état incohérent.
// On ferme d'abord le client WhatsApp (browser.close() propre + arrêt de la
// sauvegarde périodique), puis on ferme Redis, avant de sortir.
let shuttingDown = false;
async function shutdown(signal) {
    if (shuttingDown) return;
    shuttingDown = true;
    console.log(`[WhatsApp] ${signal} reçu — fermeture propre en cours…`);
    try {
        if (activeClient) await activeClient.destroy();
    } catch (err) {
        console.warn('[WhatsApp] Erreur pendant la fermeture du client :', err.message);
    }
    try {
        await redis.quit();
    } catch (err) {
        console.warn('[WhatsApp] Erreur pendant la fermeture de Redis :', err.message);
    }
    process.exit(0);
}
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('SIGINT', () => shutdown('SIGINT'));
