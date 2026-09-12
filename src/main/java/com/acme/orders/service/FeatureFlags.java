package com.acme.orders.service;

public interface FeatureFlags {
    boolean isEnabled(String flag);
}
