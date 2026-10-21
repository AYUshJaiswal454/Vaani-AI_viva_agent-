// Audio objects created on a user gesture in the preparation step and reused in the viva room.
import { Mic, Player } from "../voice";

export const shared: { mic: Mic | null; player: Player | null } = { mic: null, player: null };

export async function ensureAudio(): Promise<{ mic: Mic; player: Player }> {
  if (!shared.player) shared.player = new Player();
  await shared.player.unlock();
  if (!shared.mic) {
    const mic = new Mic();
    await mic.start();
    shared.mic = mic;
  }
  return { mic: shared.mic, player: shared.player };
}
