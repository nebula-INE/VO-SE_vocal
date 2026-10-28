import { useState, useEffect } from 'react';

export type DeviceType = 'desktop' | 'tablet' | 'phone';
export type Orientation = 'portrait' | 'landscape';

export interface ResponsiveInfo {
  deviceType: DeviceType;
  isDesktop: boolean;
  isTablet: boolean;
  isPhone: boolean;
  isTouch: boolean;
  orientation: Orientation;
  width: number;
  height: number;
  safeAreaBottom: number;
}

export function useResponsive(): ResponsiveInfo {
  const [state, setState] = useState<ResponsiveInfo>(() => {
    if (typeof window === 'undefined') {
      return {
        deviceType: 'desktop',
        isDesktop: true,
        isTablet: false,
        isPhone: false,
        isTouch: false,
        orientation: 'landscape',
        width: 1280,
        height: 800,
        safeAreaBottom: 0,
      };
    }

    const width = window.innerWidth;
    const height = window.innerHeight;
    const isTouch = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
    const orientation = width >= height ? 'landscape' : 'portrait';

    let deviceType: DeviceType = 'desktop';
    if (width < 768) {
      deviceType = 'phone';
    } else if (width < 1200) {
      deviceType = 'tablet';
    } else {
      deviceType = 'desktop';
    }

    return {
      deviceType,
      isDesktop: deviceType === 'desktop',
      isTablet: deviceType === 'tablet',
      isPhone: deviceType === 'phone',
      isTouch,
      orientation,
      width,
      height,
      safeAreaBottom: 0,
    };
  });

  useEffect(() => {
    const handleResize = () => {
      const width = window.innerWidth;
      const height = window.innerHeight;
      const isTouch = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
      const orientation = width >= height ? 'landscape' : 'portrait';

      let deviceType: DeviceType = 'desktop';
      if (width < 768) {
        deviceType = 'phone';
      } else if (width < 1200) {
        deviceType = 'tablet';
      } else {
        deviceType = 'desktop';
      }

      setState({
        deviceType,
        isDesktop: deviceType === 'desktop',
        isTablet: deviceType === 'tablet',
        isPhone: deviceType === 'phone',
        isTouch,
        orientation,
        width,
        height,
        safeAreaBottom: 0,
      });
    };

    window.addEventListener('resize', handleResize);
    window.addEventListener('orientationchange', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      window.removeEventListener('orientationchange', handleResize);
    };
  }, []);

  return state;
}
