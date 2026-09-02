import type { GeoPoint } from '../../domain/types.js';

const EARTH_RADIUS_METERS = 6_371_000;
export const METERS_PER_MILE = 1609.344;

/** Great-circle distance in meters (haversine). */
export function haversineMeters(a: GeoPoint, b: GeoPoint): number {
  const toRad = (deg: number) => (deg * Math.PI) / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const lat1 = toRad(a.latitude);
  const lat2 = toRad(b.latitude);

  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_METERS * Math.asin(Math.sqrt(h));
}

/** Average speeds used for ETA estimates until an OSRM/isochrone backend is wired in. */
export const TRANSPORT_SPEED_MPS: Record<'FOOT' | 'BIKE' | 'EBIKE', number> = {
  FOOT: 1.4,
  BIKE: 4.5,
  EBIKE: 6.5,
};
