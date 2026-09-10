import React from 'react';
import { k, Icons } from '../../styles/kiteUI';

interface KiteActionButtonsProps {
  onBuy?: (e: React.MouseEvent) => void;
  onSell?: (e: React.MouseEvent) => void;
  onDepth?: (e: React.MouseEvent) => void;
  onChart?: (e: React.MouseEvent) => void;
  onDelete?: (e: React.MouseEvent) => void;
  onMore?: (e: React.MouseEvent) => void;
  onAdd?: (e: React.MouseEvent) => void;
  onBasket?: (e: React.MouseEvent) => void;
  className?: string;
  variant?: 'short' | 'long';
  buyLabel?: string;
  sellLabel?: string;
  /**
   * Draw the action but refuse the press.
   *
   * Pass the handler AND this, rather than dropping the handler: an absent
   * button takes its slot with it, so the row's actions shift left and the
   * column stops lining up — and the absence reads as "this row has no Buy"
   * instead of "you cannot buy this one". Use `disabledHint` to say which.
   */
  buyDisabled?: boolean;
  sellDisabled?: boolean;
  disabledHint?: string;
  style?: React.CSSProperties;
}

export function KiteActionButtons({ onBuy, onSell, onDepth, onChart, onDelete, onMore, onAdd, onBasket, className, variant = 'short', buyLabel, sellLabel, buyDisabled, sellDisabled, disabledHint, style }: KiteActionButtonsProps) {
  const btnAction: React.CSSProperties = {
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    width: 24, height: 24, borderRadius: 3, cursor: 'pointer',
    fontSize: 12, border: 'none'
  };

  const buySellStyle: React.CSSProperties = variant === 'long' 
    ? {
        display: 'flex', alignItems: 'center', justifyContent: 'center', 
        width: 125, height: 32, borderRadius: 3, padding: 0, 
        border: 'none', cursor: 'pointer', 
        fontSize: 12, fontWeight: 700, letterSpacing: '0.5px', color: '#ffffff'
      }
    : { 
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        width: 22, height: 22, minWidth: 22, borderRadius: 3, padding: 0, cursor: 'pointer',
        fontSize: 11, fontWeight: 700, border: 'none', color: '#ffffff'
      };

  const iconBtnStyle: React.CSSProperties = {
    ...btnAction, background: 'transparent', color: k.dim, padding: 4
  };

  /**
   * A button that cannot be pressed, still drawn.
   *
   * Dropping the handler removed the button entirely, so an ended leg's row lost
   * its Buy and every action after it shifted left — the column stopped lining
   * up, and worse, the control's absence read as "this row has no Buy" rather
   * than "you cannot buy this one, because it has ended". Disabled says which.
   */
  const disabledStyle = (base: string): React.CSSProperties => ({
    ...buySellStyle,
    // The action's OWN colour, faded — not grey.
    //
    // A grey box reads as an unknown control; a faded blue one reads as "Buy,
    // not available here", which is the whole point of drawing it at all. The
    // white-ish result of `background: k.border` lost the identity the button
    // was being kept for.
    background: base,
    opacity: 0.38,
    cursor: 'not-allowed',
  });

  return (
    <div className={className} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, ...style }} onClick={(e) => e.stopPropagation()}>
      {onBuy && (
        <button
          style={buyDisabled ? disabledStyle('var(--k-blue)') : { ...buySellStyle, background: 'var(--k-blue)' }}
          title={buyDisabled ? (disabledHint || 'Not available on this row') : (buyLabel || 'Buy')}
          disabled={buyDisabled}
          onClick={buyDisabled ? undefined : onBuy}
        >
          {buyLabel || (variant === 'long' ? 'BUY' : 'B')}
        </button>
      )}
      {onSell && (
        <button
          style={sellDisabled ? disabledStyle('var(--k-orange)') : { ...buySellStyle, background: 'var(--k-orange)' }}
          title={sellDisabled ? (disabledHint || 'Not available on this row') : (sellLabel || 'Sell')}
          disabled={sellDisabled}
          onClick={sellDisabled ? undefined : onSell}
        >
          {sellLabel || (variant === 'long' ? 'SELL' : 'S')}
        </button>
      )}
      {onChart && (
        <button style={iconBtnStyle} title="Chart" onClick={onChart}>
          <Icons.Chart />
        </button>
      )}
      {onDepth && (
        <button style={iconBtnStyle} title="Market Depth" onClick={onDepth}>
          <Icons.Depth />
        </button>
      )}
      {onDelete && (
        <button style={iconBtnStyle} title="Delete" onClick={onDelete}>
          <Icons.Trash />
        </button>
      )}
      {onMore && (
        <button style={iconBtnStyle} title="More" onClick={onMore}>
          <Icons.More />
        </button>
      )}
      {onAdd && (
        <button style={{ ...btnAction, background: k.green, color: 'var(--k-bg)', fontSize: 16, fontWeight: 600 }} title="Add to watchlist" onClick={onAdd}>
          +
        </button>
      )}
      {onBasket && (
        <button style={iconBtnStyle} title="Add to basket" onClick={onBasket}>
          <Icons.Basket />
        </button>
      )}
    </div>
  );
}
