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

    _zipPath(session) {
        return path.join(this.dataPath, `${session}.zip`);
    }

    async sessionExists({ session }) {
        return (await this.redis.exists(this._key(session))) === 1;
    }

    async save({ session }) {
        // RemoteAuth vient d'écrire l'archive à cet emplacement via compressSession().
        const data = await fs.promises.readFile(this._zipPath(session));
        // ioredis stocke le Buffer tel quel (binaire) — SET écrase l'ancienne valeur.
        await this.redis.set(this._key(session), data);
    }

    async extract({ session, path: destPath }) {
        // getBuffer garantit une lecture binaire fidèle (pas de décodage UTF-8).
        const data = await this.redis.getBuffer(this._key(session));
        if (!data) {
            throw new Error(`RedisStore : session "${session}" introuvable dans Redis`);
        }
        await fs.promises.writeFile(destPath, data);
    }

    async delete({ session }) {
        await this.redis.del(this._key(session));
    }
}

module.exports = { RedisStore };
