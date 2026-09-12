package com.acme.orders.domain;

import java.math.BigDecimal;
import java.util.List;

public class Order {
    private Long id; private String customerId; private OrderStatus status; private BigDecimal total; private List<OrderItem> items; private String paymentId;
    public static Order from(CreateOrderRequest request) { Order o = new Order(); o.items = request.getItems(); o.total = request.total(); return o; }
    public Long getId() { return id; }
    public OrderStatus getStatus() { return status; }
    public void setStatus(OrderStatus s) { this.status = s; }
    public BigDecimal getTotal() { return total; }
    public List<OrderItem> getItems() { return items; }
    public String getPaymentId() { return paymentId; }
    public void setPaymentId(String p) { this.paymentId = p; }
}
