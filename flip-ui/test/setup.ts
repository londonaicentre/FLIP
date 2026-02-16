/*
 * Copyright (c) Guy's and St Thomas' NHS Foundation Trust & King's College London
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *     http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { config } from '@vue/test-utils'
import { afterAll, afterEach, beforeAll, vi } from 'vitest'

// Global test setup
beforeAll(async () => {
  console.log('Setting up test environment...')

  // Configure Vue Test Utils globally
  config.global.stubs = {
    // Vue Router components
    'router-link': true,
    'router-view': true,

    // Headless UI components
    'Dialog': true,
    'DialogOverlay': true,
    'DialogPanel': true,
    'DialogTitle': true,
    'DialogDescription': true,
    'TransitionRoot': true,
    'TransitionChild': true,

    // Vue built-in components
    'Teleport': true,

    // CodeMirror component
    'Codemirror': true,
  }

  // Mock window.matchMedia for responsive components
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation(query => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })

  // Mock ResizeObserver
  global.ResizeObserver = vi.fn().mockImplementation(() => ({
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: vi.fn(),
  }))

  // Mock IntersectionObserver
  global.IntersectionObserver = vi.fn().mockImplementation(() => ({
    observe: vi.fn(),
    unobserve: vi.fn(),
    disconnect: vi.fn(),
  }))
})

// Cleanup after all tests
afterAll(async () => {
  console.log('Cleaning up test environment...')
})

// Cleanup after each test
afterEach(async () => {
  // Clear all mocks to ensure test isolation
  vi.clearAllMocks()
})

// Configure console output for tests
if (process.env.NODE_ENV === 'test') {
  global.console = {
    ...console,
    log: vi.fn(),
    warn: vi.fn(),
    error: console.error, // Keep errors visible
  }
}
