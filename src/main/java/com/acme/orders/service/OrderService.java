package com.acme.orders.service;

import com.acme.orders.domain.*;
import java.util.List;

public interface OrderService {
    Order getOrder(Long id);
    Order placeOrder(CreateOrderRequest request, String requestId);
    void cancel(Long id);
    List<Order> findForCustomer(String customerId, int limit);
}
