import type { Transition, Variants } from 'framer-motion'

export const smoothSpring: Transition = { type: 'spring', stiffness: 420, damping: 34, mass: 0.8 }
export const quickEase: Transition = { duration: 0.18, ease: [0.2, 0, 0, 1] }
export const softSpring: Transition = { type: 'spring', stiffness: 260, damping: 28, mass: 0.9 }
export const pressSpring: Transition = { type: 'spring', stiffness: 520, damping: 32, mass: 0.6 }
export const settleSpring: Transition = { type: 'spring', stiffness: 300, damping: 32, mass: 0.85 }

export function fadeSlide(reduced: boolean, y = 8): Variants {
  return {
    hidden: reduced ? { opacity: 0 } : { opacity: 0, y },
    show: { opacity: 1, y: 0, transition: quickEase },
    exit: reduced ? { opacity: 0 } : { opacity: 0, y: -Math.max(4, y / 2), transition: quickEase },
  }
}

export function popIn(reduced: boolean, scale = 0.98): Variants {
  return {
    hidden: reduced ? { opacity: 0 } : { opacity: 0, scale },
    show: { opacity: 1, scale: 1, transition: smoothSpring },
    exit: reduced ? { opacity: 0 } : { opacity: 0, scale, transition: quickEase },
  }
}

export function countSwap(reduced: boolean): Variants {
  return {
    hidden: reduced ? { opacity: 0 } : { opacity: 0, y: 6, filter: 'blur(2px)' },
    show: { opacity: 1, y: 0, filter: 'blur(0px)', transition: quickEase },
    exit: reduced ? { opacity: 0 } : { opacity: 0, y: -6, filter: 'blur(2px)', transition: quickEase },
  }
}

export function staggerList(reduced: boolean, distance = 5): Variants {
  return {
    hidden: reduced ? { opacity: 0 } : { opacity: 0, y: distance },
    show: (i = 0) => ({
      opacity: 1,
      y: 0,
      transition: {
        ...quickEase,
        delay: reduced ? 0 : Math.min(i, 8) * 0.022,
      },
    }),
    exit: reduced ? { opacity: 0 } : { opacity: 0, y: -Math.max(3, distance / 2), transition: quickEase },
  }
}

export function actionConfirm(reduced: boolean): Variants {
  return {
    idle: { scale: 1 },
    active: reduced
      ? { scale: 1 }
      : {
          scale: [1, 0.98, 1.015, 1],
          transition: { duration: 0.34, ease: [0.2, 0, 0, 1] },
        },
  }
}

export function statusFlash(reduced: boolean): Variants {
  return {
    hidden: reduced ? { opacity: 0 } : { opacity: 0, scaleX: 0.2 },
    show: reduced
      ? { opacity: [0, 0.5, 0], transition: { duration: 0.28, ease: [0.2, 0, 0, 1] } }
      : {
          opacity: [0, 0.65, 0],
          scaleX: [0.2, 1, 1],
          transition: { duration: 0.58, ease: [0.2, 0, 0, 1] },
        },
    exit: { opacity: 0, transition: { duration: 0.1 } },
  }
}

export function liveMetricSwap(reduced: boolean): Variants {
  return {
    hidden: reduced ? { opacity: 0 } : { opacity: 0, y: 5, scale: 0.98 },
    show: reduced
      ? { opacity: 1, transition: quickEase }
      : { opacity: 1, y: 0, scale: 1, transition: softSpring },
    exit: reduced ? { opacity: 0 } : { opacity: 0, y: -5, scale: 0.98, transition: quickEase },
  }
}
