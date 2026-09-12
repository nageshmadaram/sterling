import { describe, expect, it } from 'vitest';
import { SECTION_IDS, isSectionId, openSettingsSection, resolveSectionId, type SectionId } from '../config/registry';

/**
 * `SECTION_IDS` is the runtime half of the `SectionId` union, and nothing forced
 * the two to agree. When `'diagnostics'` was added to the type but not the array,
 * `isSectionId('diagnostics')` returned false — which silently made
 * `openSettingsSection` a no-op and stopped the section being restored on reload.
 */
describe('settings section registry', () => {
  // A Record over the union: TypeScript fails to compile if a SectionId is missing,
  // so the compiler enforces the list even before the runtime assertions below.
  const EVERY_SECTION: Record<SectionId, true> = {
    account: true, truedata: true, diagnostics: true, mode: true, manualRules: true,
    autoRules: true, engine: true, navigator: true, adaptiveEdge: true,
    gammaMove: true, intraday: true, snapback: true,
    markets: true, notifications: true,
    experience: true, dataLake: true,
  };

  it('lists every declared section id', () => {
    expect([...SECTION_IDS].sort()).toEqual(Object.keys(EVERY_SECTION).sort());
  });

  it('recognises every declared section', () => {
    for (const id of Object.keys(EVERY_SECTION)) {
      expect(isSectionId(id)).toBe(true);
      expect(resolveSectionId(id)).toBe(id);
    }
  });

  it.each(['diagnostics', 'gammaMove', 'intraday'] as SectionId[])(
    'can deep-link and persist %s',
    (section) => {
      localStorage.clear();
      openSettingsSection(section);
      expect(localStorage.getItem('kite_connect_section')).toBe(section);
    },
  );

  it('rejects an unknown id rather than persisting a typo', () => {
    localStorage.clear();
    expect(isSectionId('orbOption')).toBe(false);
    expect(resolveSectionId('orbOption')).toBeNull();
    openSettingsSection('orbOption' as SectionId);
    expect(localStorage.getItem('kite_connect_section')).toBeNull();
  });
});
