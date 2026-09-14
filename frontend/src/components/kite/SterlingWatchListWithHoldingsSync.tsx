import React from 'react';
import { SterlingWatchList } from './SterlingWatchList';

export function SterlingWatchListWithHoldingsSync({
  onOpenInstrument,
}: {
  onOpenInstrument?: (symbol: string, defaultTab: 'chart' | 'option-chain') => void;
}) {
  return <SterlingWatchList onOpenInstrument={onOpenInstrument} />;
}

export default SterlingWatchListWithHoldingsSync;
