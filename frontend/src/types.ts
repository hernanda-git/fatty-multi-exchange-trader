import type {RecordData, Principal, Envelope, Capability, ApiError} from './api';

export type {RecordData, Principal, Envelope, Capability, ApiError};

export type PrimaryDestination = 'overview' | 'trading' | 'orders' | 'signals' | 'activity' | 'settings';

export type RouteInfo = {
  destination: PrimaryDestination;
  screen: string;
  subSection: string;
  id: string;
  readSection: string;
  resourceId: string;
  rawPath: string;
};

export type PendingOperation = {
  key: string;
  action: string;
  payload: RecordData;
};

export function isObjectRecord(value: unknown): value is RecordData {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export type FailureState = 'loading' | 'empty' | 'filtered_empty' | 'stale' | 'partial' | 'error' | 'unavailable' | 'normal';
