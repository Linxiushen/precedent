// Copyright 2026 The Precedent authors.
// Licensed under the Apache License, Version 2.0 (the "License"); you may not
// use this file except in compliance with the License. You may obtain a copy of
// the License at http://www.apache.org/licenses/LICENSE-2.0
// SPDX-License-Identifier: Apache-2.0
//
// The real `definePluginEntry` is an identity function with a type signature:
// it exists so the host can infer the entry's shape and so a plugin fails at
// author time rather than at load time. This stub is the same identity, which
// is why a test that drives `activate` through it is testing the real thing.
export function definePluginEntry(entry) {
  return entry;
}
