package com.acme.orders.service;

import com.acme.orders.domain.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
public class FraudScorer {
    private final RestTemplate restTemplate;
    private final String fraudUrl;

    public FraudScorer(RestTemplate restTemplate, String fraudUrl) {
        this.restTemplate = restTemplate;
        this.fraudUrl = fraudUrl;
    }

    public int score(Order order) {
        FraudResponse resp = restTemplate.postForObject(fraudUrl + "/score", order, FraudResponse.class);
        return resp.getScore();
    }
}
