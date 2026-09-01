'use strict';

const fs = require('fs');
const path = require('path');

/**
 * Store RemoteAuth (whatsapp-web.js) adossé à Redis.
 *
 * RemoteAuth se charge lui-même de compresser/décompresser la session dans un
 * fichier `${dataPath}/${session}.zip` ; ce store ne fait que déplacer ce blob
 * zip entre le disque local (éphémère) et Redis (persistant). La session
 * WhatsApp survit ainsi à tout redémarrage/rebuild du container, tant que
 * Redis conserve la clé (persistance AOF activée côté redis, voir
 * docker-compose.yml).
 *
 * Contrat attendu par RemoteAuth :
 *   - sessionExists({ session })  → bool
 *   - save({ session })           → lit ${dataPath}/${session}.zip et le stocke
 *   - extract({ session, path })  → écrit le zip stocké vers `path`
 *   - delete({ session })         → supprime la session stockée
 *
 * `save` ne reçoit PAS le chemin du zip (seulement le nom de session) : on
 * reconstruit donc `${dataPath}/${session}.zip` à partir du même `dataPath`
 * que celui passé à RemoteAuth, sans dépendre du répertoire courant.
 *
 * ── Une archive de session peut être corrompue ────────────────────────────────
 *
 * Constaté en vrai le 1er septembre 2026 : l'archive active était un ZIP
 * structurellement valide — signature de début et répertoire central corrects —
 * mais son extraction cassait à la 52ᵉ entrée sur `invalid signature`. Une
 * entrée avait été lue par `archiver` PENDANT que Chromium l'écrivait.
 *
 * Conséquence : chaque reconnexion relisait la même archive et échouait de la
 * même façon. Cent trente-quatre tentatives en deux heures, à raison d'une par
 * minute, sans que rien ne distingue cet échec définitif d'un échec passager.
 *
 * D'où les deux ajouts de ce store :
 *
 *   • `save` conserve la PRÉCÉDENTE archive avant d'écrire la nouvelle. Une
 *     corruption n'atteint donc jamais les deux à la fois : celle qui est
 *     écrasée a déjà été extraite avec succès au démarrage.
 *   • `extract` vérifie l'archive avant de la livrer, et bascule sur la
 *     précédente si elle est illisible. Le service repart au lieu de boucler.
 */
class RedisStore {
    /**
     * @param {object} opts
     * @param {import('ioredis').Redis} opts.redis - client ioredis connecté
     * @param {string} opts.dataPath - même dataPath que celui passé à RemoteAuth
     * @param {string} [opts.keyPrefix] - préfixe des clés Redis
     */
    constructor({ redis, dataPath, keyPrefix = 'wwebjs:session:' }) {
        if (!redis) throw new Error('RedisStore : client redis requis');
        if (!dataPath) throw new Error('RedisStore : dataPath requis');
        this.redis = redis;
        this.dataPath = dataPath;
        this.keyPrefix = keyPrefix;
    }

    _key(session) {
        return `${this.keyPrefix}${session}`;
    }

    /** Clé de l'archive précédente — le filet du repli. */
    _keyPrecedente(session) {
        return `${this.keyPrefix}${session}.precedente`;
    }

    _zipPath(session) {
        return path.join(this.dataPath, `${session}.zip`);
    }

    /**
     * L'archive est-elle extractible de bout en bout ?
     *
     * Vérifier la seule signature de fin ne suffit pas : celle de l'archive
     * corrompue observée était correcte. Il faut parcourir les entrées, ce qui
     * est le seul moyen de rencontrer une entrée illisible.
     *
     * Le parcours ne décompresse rien (`autodrain`) : sur 85 Mo, il coûte
     * quelques centaines de millisecondes, une fois par démarrage.
     */
    async _archiveLisible(buffer) {
        const unzipper = require('unzipper');
        const { Readable } = require('stream');
        return new Promise((resolve) => {
            let entrees = 0;
            Readable.from(buffer)
                .pipe(unzipper.Parse())
                .on('entry', (e) => {
                    entrees += 1;
                    e.autodrain();
                })
                .on('close', () => resolve({ ok: true, entrees }))
                .on('error', (err) => resolve({ ok: false, entrees, erreur: err.message }));
        });
    }

    async sessionExists({ session }) {
        return (await this.redis.exists(this._key(session))) === 1;
    }

    async save({ session }) {
        // RemoteAuth vient d'écrire l'archive à cet emplacement via compressSession().
        const data = await fs.promises.readFile(this._zipPath(session));

        // L'archive sortante devient le filet. Elle a été extraite avec succès
        // au démarrage de ce processus — donc elle est bonne, et on le sait.
        // Sans ce décalage, une sauvegarde corrompue écrase la seule copie
        // valide et il ne reste plus qu'un scan de QR.
        const sortante = await this.redis.getBuffer(this._key(session));
        if (sortante) {
            await this.redis.set(this._keyPrecedente(session), sortante);
        }

        // ioredis stocke le Buffer tel quel (binaire) — SET écrase l'ancienne valeur.
        await this.redis.set(this._key(session), data);
    }

    async extract({ session, path: destPath }) {
        // getBuffer garantit une lecture binaire fidèle (pas de décodage UTF-8).
        const data = await this.redis.getBuffer(this._key(session));
        if (!data) {
            throw new Error(`RedisStore : session "${session}" introuvable dans Redis`);
        }

        const verdict = await this._archiveLisible(data);
        if (verdict.ok) {
            await fs.promises.writeFile(destPath, data);
            return;
        }

        // L'archive active est illisible. La relivrer ferait échouer
        // l'initialisation, et la suivante, et toutes les suivantes.
        console.error(
            `[RedisStore] Archive de session illisible après ${verdict.entrees} entrées : ${verdict.erreur}`,
        );

        const precedente = await this.redis.getBuffer(this._keyPrecedente(session));
        if (!precedente) {
            // On écarte l'archive morte : au prochain démarrage, `sessionExists`
            // rendra faux et RemoteAuth demandera un QR. Mieux vaut un scan
            // qu'une boucle silencieuse d'une tentative par minute.
            await this.redis.rename(this._key(session), `${this._key(session)}.corrompue`);
            throw new Error(
                `RedisStore : archive de session "${session}" corrompue et aucune précédente ` +
                    `disponible — un nouveau scan du QR code est nécessaire. ` +
                    `L'archive fautive est conservée sous "${this._key(session)}.corrompue".`,
            );
        }

        const verdictPrec = await this._archiveLisible(precedente);
        if (!verdictPrec.ok) {
            await this.redis.rename(this._key(session), `${this._key(session)}.corrompue`);
            throw new Error(
                `RedisStore : archive de session "${session}" ET sa précédente sont corrompues ` +
                    `— un nouveau scan du QR code est nécessaire.`,
            );
        }

        console.warn(
            `[RedisStore] Repli sur l'archive précédente (${verdictPrec.entrees} entrées, ` +
                `${(precedente.length / 1048576).toFixed(1)} Mo) — la session est récupérée sans rescan.`,
        );
        // La précédente devient l'active : la corrompue est écartée, pas perdue.
        await this.redis.rename(this._key(session), `${this._key(session)}.corrompue`);
        await this.redis.set(this._key(session), precedente);
        await fs.promises.writeFile(destPath, precedente);
    }

    async delete({ session }) {
        await this.redis.del(this._key(session));
    }
}

module.exports = { RedisStore };
