import { describe, it, expect } from 'vitest';
import { magnet, stepTo, fineStep, pullFor, pulls } from './snap';

// Reading speed: 0.5x to 2x, 0.01 apart, pulled onto every 0.05 and harder onto 1x.
const SPEED = { min: 0.5, max: 2, fine: 0.01, every: 0.05, home: [1], pull: 0.016, homePull: 0.029 };

describe('magnet: a dragged value is pulled onto the round one near it', () => {
  it('near a round value, lands on it', () => {
    expect(magnet(1.112, SPEED)).toBe(1.1);
    expect(magnet(1.139, SPEED)).toBe(1.15);
  });

  it('between the pulls, moves freely, to the fine step', () => {
    expect(magnet(1.121, SPEED)).toBe(1.12);
    expect(magnet(1.128, SPEED)).toBe(1.13);
  });

  it('the home value pulls harder: a little further away still lands on 1x', () => {
    expect(magnet(1.026, SPEED)).toBe(1);
    expect(magnet(1.126, SPEED)).toBe(1.13);   // the same distance from 1.10 is free
  });

  it('never leaves the range, and has no floating-point dust', () => {
    expect(magnet(2.3, SPEED)).toBe(2);
    expect(magnet(0.41, SPEED)).toBe(0.5);
    expect(magnet(0.7 + 0.1 + 0.02, SPEED)).toBe(0.82);
  });

  it('works on whole numbers too: milliseconds pulled onto every 100', () => {
    const silence = { min: 100, max: 5000, fine: 10, every: 100, pull: 40 };
    expect(magnet(731, silence)).toBe(700);
    expect(magnet(752, silence)).toBe(750);
  });
});

describe('stepTo: a key or a screen reader moves to the next round value', () => {
  it('from a round value, to the next one', () => {
    expect(stepTo(1.1, 1, SPEED)).toBe(1.15);
    expect(stepTo(1.1, -1, SPEED)).toBe(1.05);
  });

  it('from in between, to the nearest round value that way', () => {
    expect(stepTo(1.12, 1, SPEED)).toBe(1.15);
    expect(stepTo(1.12, -1, SPEED)).toBe(1.1);
  });

  it('stops at the ends', () => {
    expect(stepTo(2, 1, SPEED)).toBe(2);
    expect(stepTo(0.5, -1, SPEED)).toBe(0.5);
  });
});

describe('fineStep: Shift with a key moves by the fine step', () => {
  it('one fine step, within the range', () => {
    expect(fineStep(1.1, 1, SPEED)).toBe(1.11);
    expect(fineStep(0.5, -1, SPEED)).toBe(0.5);
  });
});

describe('pullFor: the pull is measured in pixels, so it feels the same on any width', () => {
  it('turns pixels on the track into the value they cover', () => {
    expect(pullFor(5, 500, 0.5, 2)).toBeCloseTo(0.015);
    expect(pullFor(5, 250, 0.5, 2)).toBeCloseTo(0.03);   // a phone's shorter track: a wider pull in value
  });

  it('a track with no width yet (not laid out) uses a typical one', () => {
    expect(pullFor(5, 0, 0.5, 2)).toBeCloseTo(0.025);
  });
});

describe("pulls: never so wide that the values in between can't be reached", () => {
  it('on a roomy track, the pixels asked for', () => {
    const { pull, homePull } = pulls(470, { min: 0.5, max: 2, every: 0.05 }, 5, 9);
    expect(pull).toBeCloseTo(pullFor(5, 470, 0.5, 2));
    expect(homePull).toBeLessThanOrEqual(0.05 / 2);
  });

  // 100 ms of a 4.9 s range is about 10 px on a desktop track: a 5 px pull would cover every position.
  it('where round values are close together, at most 40% of the gap each side, so part of each gap stays free', () => {
    const { pull } = pulls(470, { min: 100, max: 5000, every: 100 }, 5, 9);
    expect(pull).toBeLessThan(50);
    expect(magnet(1250, { min: 100, max: 5000, fine: 10, every: 100, pull })).toBe(1250);
    expect(magnet(1220, { min: 100, max: 5000, fine: 10, every: 100, pull })).toBe(1200);
  });
});
