const { Client, LocalAuth } = require('whatsapp-web.js');
const express = require('express');
const fs = require('fs');
const path = require('path');
const qrcodeTerminal = require('qrcode-terminal');
const QRCode = require('qrcode');

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 3000;
const SESSION_PATH = process.env.SESSION_PATH || '/app/session';

// Répertoire Chromium créé par LocalAuth (sans clientId → nom "session")
const CHROMIUM_USER_DATA_DIR = path.join(SESSION_PATH, 'session');

let isReady = false;
let currentQr = null;
let activeClient = null;
let restartCount = 0;

/**
 * Supprime les fichiers Singleton* laissés par Chromium après un arrêt brutal.
 * Sans ce nettoyage, Chromium détecte une instance existante et refuse de
 * démarrer, provoquant un crash-loop au redémarrage du container Docker.
 */
function cleanChromiumLocks() {
    if (!fs.existsSync(CHROMIUM_USER_DATA_DIR)) return;

    let removed = 0;
    try {
        const entries = fs.readdirSync(CHROMIUM_USER_DATA_DIR, { withFileTypes: true });
        for (const entry of entries) {
            if (!entry.name.startsWith('Singleton')) continue;
            const lockPath = path.join(CHROMIUM_USER_DATA_DIR, entry.name);
            try {
                // lstatSync avant unlink car SingletonLock est un symlink
                fs.lstatSync(lockPath);
                fs.unlinkSync(lockPath);
                removed++;
            } catch (_) {
                // Déjà supprimé ou lien mort — ignoré
            }
        }
    } catch (err) {
        console.warn('[WhatsApp] Impossible de scanner le répertoire session :', err.message);
        return;
    }

    if (removed > 0) {
        console.log(`[WhatsApp] ${removed} lock(s) Chromium supprimé(s) avant démarrage`);
    }
}

function startClient() {
    cleanChromiumLocks();

    const client = new Client({
        authStrategy: new LocalAuth({ dataPath: SESSION_PATH }),
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

    client.on('qr', (qr) => {
        currentQr = qr;
        qrcodeTerminal.generate(qr, { small: true });
        console.log(`[WhatsApp] QR code prêt — scannez-le sur http://localhost:${PORT}/qr`);
    });

    client.on('authenticated', () => {
        console.log('[WhatsApp] Session authentifiée');
    });

    client.on('ready', () => {
        isReady = true;
        restartCount = 0;
        currentQr = null;
        console.log('[WhatsApp] Client prêt — envoi de messages activé');
    });

    client.on('auth_failure', (msg) => {
        console.error('[WhatsApp] Échec d\'authentification :', msg);
        isReady = false;
        scheduleRestart(client);
    });

    client.on('disconnected', async (reason) => {
        if (activeClient !== client) return; // un nouveau cycle a déjà commencé
        console.warn('[WhatsApp] Déconnecté :', reason);
        isReady = false;
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
    res.json({ ready: isReady });
});

app.get('/qr', async (_req, res) => {
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

app.post('/send', async (req, res) => {
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

app.post('/send-with-pdf', async (req, res) => {
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
