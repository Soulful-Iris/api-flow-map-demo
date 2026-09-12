package com.acme.orders.service;

import com.acme.orders.domain.*;
import com.acme.orders.repo.OrderRepository;
import com.acme.orders.client.PaymentClient;
import com.acme.orders.client.InventoryClient;
import com.acme.orders.client.NotificationClient;
import com.acme.orders.messaging.OrderEventPublisher;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import java.util.List;
import java.math.BigDecimal;

@Service
public class OrderServiceImpl implements OrderService {
    private static final Logger log = LoggerFactory.getLogger(OrderServiceImpl.class);
    private static final BigDecimal REVIEW_THRESHOLD = new BigDecimal("1000");

    private final OrderRepository orderRepository;
    private final PaymentClient paymentClient;
    private final InventoryClient inventoryClient;
    private final OrderEventPublisher eventPublisher;
    private final FraudScorer fraudScorer;
    private final FeatureFlags featureFlags;
    private final NotificationClient notificationClient;

    public OrderServiceImpl(OrderRepository orderRepository, PaymentClient paymentClient, InventoryClient inventoryClient,
                            OrderEventPublisher eventPublisher, FraudScorer fraudScorer, FeatureFlags featureFlags) {
        this.orderRepository = orderRepository;
        this.paymentClient = paymentClient;
        this.inventoryClient = inventoryClient;
        this.eventPublisher = eventPublisher;
        this.fraudScorer = fraudScorer;
        this.featureFlags = featureFlags;
    }

    @Override
    @Cacheable("orders")
    public Order getOrder(Long id) {
        log.debug("loading order {}", id);
        return orderRepository.findById(id).orElse(null);
    }

    @Override
    @Transactional
    public Order placeOrder(CreateOrderRequest request, String requestId) {
        validateRequest(request);
        Order order = Order.from(request);
        InventoryStatus stock = inventoryClient.reserve(order.getItems());
        if (!stock.isAvailable()) {
            throw new OutOfStockException(stock.getMissingSkus());
        }
        if (order.getTotal().compareTo(REVIEW_THRESHOLD) > 0) {
            int score = fraudScorer.score(order);
            if (score > 60) {
                order.setStatus(OrderStatus.UNDER_REVIEW);
                orderRepository.save(order);
                eventPublisher.publish(new OrderFlaggedEvent(order.getId(), score));
                return order;
            }
        }
        try {
            PaymentResult result = paymentClient.charge(new ChargeRequest(order.getTotal(), request.getPaymentToken(), requestId));
            order.setPaymentId(result.getPaymentId());
        } catch (PaymentDeclinedException e) {
            inventoryClient.release(order.getItems());
            throw e;
        }
        order.setStatus(OrderStatus.CONFIRMED);
        Order saved = orderRepository.save(order);
        eventPublisher.publish(new OrderPlacedEvent(saved.getId()));
        if (featureFlags.isEnabled("order-confirmation-email")) {
            notificationClient.sendConfirmation(saved.getId(), request.getEmail());
        }
        return saved;
    }

    @Override
    @Transactional
    public void cancel(Long id) {
        Order order = orderRepository.findById(id).orElseThrow(() -> new OrderNotFoundException(id));
        switch (order.getStatus()) {
            case CONFIRMED:
                paymentClient.refund(order.getPaymentId());
                inventoryClient.release(order.getItems());
                break;
            case UNDER_REVIEW:
                break;
            default:
                throw new IllegalStateException("cannot cancel " + order.getStatus());
        }
        order.setStatus(OrderStatus.CANCELLED);
        orderRepository.save(order);
        eventPublisher.publish(new OrderCancelledEvent(id));
    }

    @Override
    public List<Order> findForCustomer(String customerId, int limit) {
        return orderRepository.findByCustomerIdOrderByCreatedAtDesc(customerId, limit);
    }

    private void validateRequest(CreateOrderRequest request) {
        if (request.getItems() == null || request.getItems().isEmpty()) {
            throw new IllegalArgumentException("order must contain items");
        }
    }
}
