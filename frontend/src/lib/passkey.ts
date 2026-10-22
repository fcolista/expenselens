import { api } from './api';

const toBytes = (value: string): ArrayBuffer => {
  const base64 = value.replace(/-/g, '+').replace(/_/g, '/').padEnd(Math.ceil(value.length / 4) * 4, '=');
  const binary = atob(base64);
  return Uint8Array.from(binary, (char) => char.charCodeAt(0)).buffer;
};

const toText = (buffer: ArrayBuffer | null): string | null => {
  if (!buffer) return null;
  let binary = '';
  new Uint8Array(buffer).forEach((byte) => { binary += String.fromCharCode(byte); });
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};

export const passkeysSupported = () => typeof window !== 'undefined' && 'PublicKeyCredential' in window && !!navigator.credentials;

export class PasskeyCancelled extends Error {}

async function ask<T>(action: Promise<T>): Promise<T> {
  try { return await action; }
  catch (e) {
    if (e instanceof DOMException && (e.name === 'NotAllowedError' || e.name === 'AbortError')) throw new PasskeyCancelled();
    throw e;
  }
}

export async function registerPasskey(name: string): Promise<void> {
  const { options, challenge_token } = await api.passkeyRegisterOptions();
  const credential = await ask(navigator.credentials.create({
    publicKey: {
      ...options,
      challenge: toBytes(options.challenge),
      user: { ...options.user, id: toBytes(options.user.id) },
      excludeCredentials: (options.excludeCredentials ?? []).map((item: { id: string }) => ({ ...item, id: toBytes(item.id) }))
    } as PublicKeyCredentialCreationOptions
  })) as PublicKeyCredential | null;
  if (!credential) throw new PasskeyCancelled();
  const response = credential.response as AuthenticatorAttestationResponse;
  await api.passkeyRegisterVerify({
    challenge_token, name,
    credential: {
      id: credential.id, rawId: toText(credential.rawId), type: credential.type,
      response: {
        clientDataJSON: toText(response.clientDataJSON), attestationObject: toText(response.attestationObject),
        transports: response.getTransports?.() ?? []
      },
      clientExtensionResults: credential.getClientExtensionResults()
    }
  });
}

export async function loginWithPasskey() {
  const { options, challenge_token } = await api.passkeyLoginOptions();
  const credential = await ask(navigator.credentials.get({
    publicKey: {
      ...options,
      challenge: toBytes(options.challenge),
      allowCredentials: (options.allowCredentials ?? []).map((item: { id: string }) => ({ ...item, id: toBytes(item.id) }))
    } as PublicKeyCredentialRequestOptions
  })) as PublicKeyCredential | null;
  if (!credential) throw new PasskeyCancelled();
  const response = credential.response as AuthenticatorAssertionResponse;
  return api.passkeyLoginVerify({
    challenge_token,
    credential: {
      id: credential.id, rawId: toText(credential.rawId), type: credential.type,
      response: {
        clientDataJSON: toText(response.clientDataJSON), authenticatorData: toText(response.authenticatorData),
        signature: toText(response.signature), userHandle: toText(response.userHandle)
      },
      clientExtensionResults: credential.getClientExtensionResults()
    }
  });
}
